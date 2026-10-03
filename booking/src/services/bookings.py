"""Брони: гость занимает места на показе (ФТ-12…ФТ-16).

Главная гарантия задания — **не продать больше мест, чем есть у хоста**. Она
держится не на проверке в коде («прочитали остаток — записали бронь»: между
чтением и записью успевает пройти чужая бронь), а на базе:

1. места занимаются условным обновлением счётчика (`take_seats`) — условие
   «мест хватает» проверяется и выполняется одним действием;
2. бронь вставляется в той же транзакции; вторая активная бронь гостя
   натыкается на уникальный индекс, и транзакция откатывает и счётчик;
3. события для писем ложатся в outbox той же транзакцией.

Если места не заняты, сервис отдельным чтением выясняет, почему именно, —
чтобы ответить точным кодом: показа нет, он начался или отменён, мест мало.
Исследование вариантов — docs/diploma/research.md, решение — ADR-21.
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from models.domain import (
    Booking,
    BookingDraft,
    BookingStatus,
    BookingView,
    GuestEntry,
    Page,
    PageRequest,
    Period,
    Screening,
)
from services.errors import (
    AlreadyBookedError,
    BookingCancelledError,
    BookingNotFoundError,
    NotBookingOwnerError,
    NotEnoughSeatsError,
    NothingToChangeError,
    NotScreeningHostError,
    OwnScreeningError,
    ScreeningClosedError,
    ScreeningNotFoundError,
    SeatsOutOfRangeError,
)
from services.letters import Letters
from services.people import NameResolver
from storage.base import AlreadyExistsError, BookingRepository, Outbox, ScreeningRepository, UnitOfWork

Clock = Callable[[], datetime]


class BookingService:
    def __init__(
        self,
        uow: UnitOfWork,
        screenings: ScreeningRepository,
        bookings: BookingRepository,
        outbox: Outbox,
        names: NameResolver,
        letters: Letters,
        max_seats: int,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._screenings = screenings
        self._bookings = bookings
        self._outbox = outbox
        self._names = names
        self._letters = letters
        self._max_seats = max_seats
        self._clock = clock

    async def book(self, guest_id: UUID, screening_id: UUID, seats: int) -> Booking:
        """Бронирует места.

        Raises:
            SeatsOutOfRangeError: мест меньше одного или больше разрешённого за раз.
            ScreeningNotFoundError, ScreeningClosedError: показа нет, он начался или отменён.
            OwnScreeningError: хост бронирует свой показ.
            AlreadyBookedError: у гостя уже есть бронь на этот показ.
            NotEnoughSeatsError: свободных мест меньше, чем просят.
        """
        self._check_seats(seats)
        screening = await self._screenings.get(screening_id)
        if screening is None:
            raise ScreeningNotFoundError
        if screening.host_id == guest_id:
            raise OwnScreeningError
        # Ранняя проверка ради понятного ответа; от гонки двух запросов одного
        # гостя защищает уникальный индекс ниже.
        if await self._bookings.active_of(screening_id, guest_id) is not None:
            raise AlreadyBookedError
        # Имя — до транзакции: поход в чужой сервис не должен держать
        # блокировку строки показа, за которой стоят другие гости.
        guest_name = await self._names.name_of(guest_id)
        async with self._uow.transaction():
            taken = await self._take(screening_id, seats)
            try:
                booking = await self._bookings.add(
                    BookingDraft(screening_id=screening_id, guest_id=guest_id, guest_name=guest_name, seats=seats),
                )
            except AlreadyExistsError as error:
                raise AlreadyBookedError from error
            await self._outbox.add(self._letters.booking_created(taken, booking))
        return booking

    async def change_seats(self, guest_id: UUID, booking_id: UUID, seats: int) -> Booking:
        """Меняет число мест в брони: добирает недостающие или возвращает лишние."""
        self._check_seats(seats)
        async with self._uow.transaction():
            booking = await self._own_active(guest_id, booking_id)
            delta = seats - booking.seats
            if delta == 0:
                raise NothingToChangeError(f'Booking already has {seats} seats')
            if delta > 0:
                screening = await self._take(booking.screening_id, delta)
            else:
                screening = await self._open_screening(booking.screening_id)
                await self._screenings.release_seats(booking.screening_id, -delta)
                screening = screening.model_copy(update={'seats_taken': screening.seats_taken + delta})
            changed = await self._bookings.change_seats(booking_id, seats)
            await self._outbox.add(self._letters.booking_changed(screening, changed))
        return changed

    async def cancel(self, guest_id: UUID, booking_id: UUID) -> Booking:
        """Отменяет бронь до начала показа и возвращает места."""
        async with self._uow.transaction():
            booking = await self._own_active(guest_id, booking_id)
            screening = await self._open_screening(booking.screening_id)
            await self._screenings.release_seats(booking.screening_id, booking.seats)
            cancelled = await self._bookings.cancel(booking_id)
            freed = screening.model_copy(update={'seats_taken': screening.seats_taken - booking.seats})
            await self._outbox.add(self._letters.booking_cancelled(freed, cancelled))
        return cancelled

    async def mine(self, guest_id: UUID, screening_id: UUID) -> Booking:
        """Активная бронь зрителя на показ — страница показа решает, что ему предложить."""
        if await self._screenings.get(screening_id) is None:
            raise ScreeningNotFoundError
        booking = await self._bookings.active_of(screening_id, guest_id)
        if booking is None:
            raise BookingNotFoundError
        return booking

    async def guests(self, host_id: UUID, screening_id: UUID) -> list[GuestEntry]:
        """Гости показа — только его хосту: это персональные данные."""
        screening = await self._screenings.get(screening_id)
        if screening is None:
            raise ScreeningNotFoundError
        if screening.host_id != host_id:
            raise NotScreeningHostError
        return await self._bookings.guests(screening_id)

    async def of_guest(self, guest_id: UUID, period: Period, page: PageRequest) -> Page[BookingView]:
        return await self._bookings.of_guest(guest_id, period, self._clock(), page)

    async def _take(self, screening_id: UUID, seats: int) -> Screening:
        taken = await self._screenings.take_seats(screening_id, seats, self._clock())
        if taken is not None:
            return taken
        # Места не заняты — выясняем причину, чтобы ответить точным кодом.
        screening = await self._open_screening(screening_id)
        raise NotEnoughSeatsError(f'Only {screening.seats_left} free seats left')

    async def _open_screening(self, screening_id: UUID) -> Screening:
        screening = await self._screenings.get(screening_id)
        if screening is None:
            raise ScreeningNotFoundError
        if not screening.is_open(self._clock()):
            raise ScreeningClosedError
        return screening

    async def _own_active(self, guest_id: UUID, booking_id: UUID) -> Booking:
        # Строка брони блокируется: два одновременных изменения одной брони
        # (двойной клик) не сдвинут счётчик дважды.
        booking = await self._bookings.get(booking_id, lock=True)
        if booking is None:
            raise BookingNotFoundError
        if booking.guest_id != guest_id:
            raise NotBookingOwnerError
        if booking.status is BookingStatus.CANCELLED:
            raise BookingCancelledError
        return booking

    def _check_seats(self, seats: int) -> None:
        if not 1 <= seats <= self._max_seats:
            raise SeatsOutOfRangeError(f'Number of seats must be between 1 and {self._max_seats}')
