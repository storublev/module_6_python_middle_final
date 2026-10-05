"""Показы: хост предлагает фильм, место и время (ФТ-5…ФТ-11).

Правила, ради которых этот слой существует:

* показ можно создать только на полнометражный фильм из каталога (ФТ-4) —
  каталог спрашивается при создании, название и обложка ложатся снимком;
* время — в будущем, не раньше чем через `min_lead_time`, и не дальше
  `max_lead_time` (ФТ-6); места — в пределах `min_capacity…max_capacity`;
* менять и отменять показ может только его хост и только до начала;
* мест нельзя сделать меньше, чем уже забронировано (ФТ-8) — это проверяет
  база одним условием, иначе между проверкой и записью пролезла бы бронь;
* отмена показа отменяет все брони и пишет гостям (ФТ-9).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from models.domain import (
    HostOffer,
    Page,
    PageRequest,
    Period,
    Screening,
    ScreeningChanges,
    ScreeningDraft,
    ScreeningStatus,
)
from services.errors import (
    CapacityBelowBookedError,
    CapacityOutOfRangeError,
    FilmNotBookableError,
    FilmNotFoundError,
    NothingToChangeError,
    NotScreeningHostError,
    ScreeningClosedError,
    ScreeningNotFoundError,
    StartsTooLateError,
    StartsTooSoonError,
)
from services.letters import Letters
from services.people import NameResolver
from storage.base import BookingRepository, Catalog, Outbox, ScreeningRepository, UnitOfWork

# Тип фильма каталога, на который можно устроить показ (ФТ-4).
BOOKABLE_FILM_TYPE = 'movie'

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ScreeningRules:
    min_capacity: int
    max_capacity: int
    min_lead_time: timedelta
    max_lead_time: timedelta


@dataclass(frozen=True)
class NewScreening:
    """Что прислал хост."""

    film_id: UUID
    starts_at: datetime
    place: str
    address: str
    description: str | None
    capacity: int


class ScreeningService:
    def __init__(
        self,
        uow: UnitOfWork,
        screenings: ScreeningRepository,
        bookings: BookingRepository,
        outbox: Outbox,
        catalog: Catalog,
        names: NameResolver,
        letters: Letters,
        rules: ScreeningRules,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._screenings = screenings
        self._bookings = bookings
        self._outbox = outbox
        self._catalog = catalog
        self._names = names
        self._letters = letters
        self._rules = rules
        self._clock = clock

    async def create(self, host_id: UUID, data: NewScreening, authorization: str | None) -> Screening:
        """Создаёт показ.

        Raises:
            StartsTooSoonError, StartsTooLateError, CapacityOutOfRangeError: показ вне правил.
            FilmNotFoundError: фильма нет в каталоге.
            FilmNotBookableError: фильм — не полнометражный.
        """
        self._check_start(data.starts_at)
        self._check_capacity(data.capacity)
        film = await self._catalog.film(data.film_id, authorization)
        if film is None:
            raise FilmNotFoundError
        if film.type != BOOKABLE_FILM_TYPE:
            raise FilmNotBookableError
        draft = ScreeningDraft(
            host_id=host_id,
            host_name=await self._names.name_of(host_id),
            film_id=film.id,
            film_title=film.title,
            film_poster=film.poster_url,
            starts_at=data.starts_at,
            place=data.place,
            address=data.address,
            description=data.description,
            capacity=data.capacity,
        )
        async with self._uow.transaction():
            screening = await self._screenings.add(draft)
        return screening

    async def get(self, screening_id: UUID) -> Screening:
        screening = await self._screenings.get(screening_id)
        if screening is None:
            raise ScreeningNotFoundError
        return screening

    async def update(self, host_id: UUID, screening_id: UUID, changes: ScreeningChanges) -> Screening:
        """Меняет показ; о смене времени или места пишет гостям.

        Raises:
            NothingToChangeError: в запросе нет ни одного поля.
            ScreeningNotFoundError, NotScreeningHostError, ScreeningClosedError.
            CapacityBelowBookedError: мест меньше, чем уже забронировано.
        """
        if changes.is_empty:
            raise NothingToChangeError
        if changes.starts_at is not None:
            self._check_start(changes.starts_at)
        if changes.capacity is not None:
            self._check_capacity(changes.capacity)
        async with self._uow.transaction():
            # Строка показа блокируется до конца транзакции: отмена и правка
            # одного показа не перемешаются, а список гостей для письма — тот
            # самый, что был на момент изменения.
            await self._owned_open(host_id, screening_id)
            updated = await self._screenings.update(screening_id, changes)
            if updated is None:
                raise CapacityBelowBookedError
            if changes.concerns_guests:
                guests = await self._bookings.guests(screening_id)
                await self._outbox.add(
                    self._letters.screening_changed(updated, (entry.booking.guest_id for entry in guests)),
                )
        return updated

    async def cancel(self, host_id: UUID, screening_id: UUID) -> Screening:
        """Отменяет показ и все его брони, пишет гостям."""
        async with self._uow.transaction():
            # Порядок блокировок тот же, что у изменения брони: показ, затем
            # брони (cancel_all). Обратный порядок у одной из операций давал бы
            # взаимную блокировку при одновременных действиях хоста и гостя.
            screening = await self._owned_open(host_id, screening_id)
            await self._screenings.cancel(screening_id)
            cancelled = await self._bookings.cancel_all(screening_id)
            await self._outbox.add(
                self._letters.screening_cancelled(screening, (booking.guest_id for booking in cancelled)),
            )
        return screening.model_copy(update={'status': ScreeningStatus.CANCELLED})

    async def upcoming(
        self, page: PageRequest, film_id: UUID | None = None, host_id: UUID | None = None,
    ) -> Page[Screening]:
        return await self._screenings.upcoming(self._clock(), page, film_id=film_id, host_id=host_id)

    async def hosts_of_film(self, film_id: UUID, page: PageRequest) -> Page[HostOffer]:
        return await self._screenings.hosts_of_film(film_id, self._clock(), page)

    async def of_host(self, host_id: UUID, period: Period, page: PageRequest) -> Page[Screening]:
        return await self._screenings.of_host(host_id, period, self._clock(), page)

    async def _owned_open(self, host_id: UUID, screening_id: UUID) -> Screening:
        screening = await self._screenings.get(screening_id, lock=True)
        if screening is None:
            raise ScreeningNotFoundError
        if screening.host_id != host_id:
            raise NotScreeningHostError
        if not screening.is_open(self._clock()):
            raise ScreeningClosedError
        return screening

    def _check_start(self, starts_at: datetime) -> None:
        now = self._clock()
        if starts_at < now + self._rules.min_lead_time:
            minutes = int(self._rules.min_lead_time.total_seconds() // 60)
            raise StartsTooSoonError(f'Screening must start at least {minutes} minutes from now')
        if starts_at > now + self._rules.max_lead_time:
            raise StartsTooLateError(
                f'Screening cannot be scheduled more than {self._rules.max_lead_time.days} days ahead',
            )

    def _check_capacity(self, capacity: int) -> None:
        if not self._rules.min_capacity <= capacity <= self._rules.max_capacity:
            raise CapacityOutOfRangeError(
                f'Capacity must be between {self._rules.min_capacity} and {self._rules.max_capacity}',
            )
