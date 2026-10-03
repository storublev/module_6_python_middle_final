"""Брони: главная гарантия задания — не больше мест, чем есть у хоста (ФТ-12…ФТ-15)."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from models.domain import BookingStatus, Period
from services.bookings import BookingService
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
from tests.unit.conftest import GUEST, HOST, MAX_SEATS, OTHER_GUEST, PAGE, World
from tests.unit.fakes import FakeBookingRepository, FakeScreeningRepository, FakeUnitOfWork


async def test_booking_takes_seats(world: World):
    """Бронь занимает места и подписана именем гостя из справочника."""
    screening = await world.screening(capacity=6)

    booking = await world.bookings.book(GUEST, screening.id, 2)

    assert (booking.seats, booking.status, booking.guest_name) == (2, BookingStatus.ACTIVE, 'Тринити')
    assert world.db.screenings[screening.id].seats_left == 4


async def test_cannot_book_more_than_left(world: World):
    """Больше мест, чем осталось, не забронировать — и ответ говорит, сколько осталось."""
    screening = await world.screening(capacity=3)
    await world.bookings.book(GUEST, screening.id, 2)

    with pytest.raises(NotEnoughSeatsError, match='Only 1 free seats left'):
        await world.bookings.book(OTHER_GUEST, screening.id, 2)
    assert world.db.screenings[screening.id].seats_taken == 2


async def test_last_seats_can_be_taken_exactly(world: World):
    """Ровно оставшиеся места забронировать можно: граница включена."""
    screening = await world.screening(capacity=3)
    await world.bookings.book(GUEST, screening.id, 2)

    await world.bookings.book(OTHER_GUEST, screening.id, 1)

    assert world.db.screenings[screening.id].seats_left == 0


async def test_concurrent_guests_do_not_oversell(world: World):
    """Двадцать гостей одновременно на пять мест: продано ровно пять, остальным — not_enough_seats."""
    screening = await world.screening(capacity=5)
    guests = [uuid4() for _ in range(20)]

    results = await asyncio.gather(
        *(world.bookings.book(guest, screening.id, 1) for guest in guests), return_exceptions=True,
    )

    booked = [result for result in results if not isinstance(result, Exception)]
    refused = [result for result in results if isinstance(result, NotEnoughSeatsError)]
    assert (len(booked), len(refused)) == (5, 15)
    assert world.db.screenings[screening.id].seats_taken == 5


async def test_host_cannot_book_own_screening(world: World):
    """Хост не бронирует места на собственный показ (ФТ-13)."""
    screening = await world.screening()

    with pytest.raises(OwnScreeningError):
        await world.bookings.book(HOST, screening.id, 1)


async def test_second_booking_is_rejected_and_seats_are_not_lost(world: World):
    """Вторая бронь гостя отклоняется, а занятые ею места возвращаются откатом."""
    screening = await world.screening(capacity=6)
    await world.bookings.book(GUEST, screening.id, 2)

    with pytest.raises(AlreadyBookedError):
        await world.bookings.book(GUEST, screening.id, 1)
    assert world.db.screenings[screening.id].seats_taken == 2


async def test_race_of_same_guest_is_caught_by_unique_index(world: World):
    """Если два запроса гостя прошли раннюю проверку, второй упирается в уникальность, и откат возвращает места."""

    class RacingBookings(FakeBookingRepository):
        async def active_of(self, screening_id, guest_id):
            # Оба запроса проверили «брони ещё нет» до того, как первый её записал.
            return None

    screening = await world.screening(capacity=6)
    service = BookingService(
        FakeUnitOfWork(world.db), FakeScreeningRepository(world.db), RacingBookings(world.db), world.outbox,
        NameResolver(world.people), Letters('https://practix.local', 'Europe/Moscow'), MAX_SEATS, world.clock,
    )
    await service.book(GUEST, screening.id, 2)

    with pytest.raises(AlreadyBookedError):
        await service.book(GUEST, screening.id, 3)
    assert world.db.screenings[screening.id].seats_taken == 2
    assert world.db.rollbacks == 1


@pytest.mark.parametrize('seats', [0, MAX_SEATS + 1])
async def test_seats_range(world: World, seats):
    """За раз — от 1 до 10 мест."""
    screening = await world.screening(capacity=50)

    with pytest.raises(SeatsOutOfRangeError):
        await world.bookings.book(GUEST, screening.id, seats)


async def test_unknown_screening(world: World):
    """Бронь на несуществующий показ — 404."""
    with pytest.raises(ScreeningNotFoundError):
        await world.bookings.book(GUEST, uuid4(), 1)


async def test_started_screening_is_not_bookable(world: World):
    """На начавшийся показ не забронировать (ФТ-13)."""
    screening = await world.screening(starts_in=timedelta(hours=1))
    world.clock.advance(timedelta(hours=1))

    with pytest.raises(ScreeningClosedError):
        await world.bookings.book(GUEST, screening.id, 1)


async def test_cancelled_screening_is_not_bookable(world: World):
    """На отменённый показ не забронировать."""
    screening = await world.screening()
    await world.screenings.cancel(HOST, screening.id)

    with pytest.raises(ScreeningClosedError):
        await world.bookings.book(GUEST, screening.id, 1)


async def test_booking_notifies_guest_and_host(world: World):
    """После брони в outbox два события: подтверждение гостю и сообщение хосту."""
    screening = await world.screening()

    await world.bookings.book(GUEST, screening.id, 2)

    events = {(e['template_code'], e['audience']['user_ids'][0]) for e in world.outbox.payloads}
    assert events == {('booking_confirmed', str(GUEST)), ('booking_host_update', str(HOST))}
    context = world.outbox.payloads[0]['context']
    assert (context['seats'], context['seats_left'], context['guest_name']) == (2, 4, 'Тринити')


async def test_increase_seats_takes_more(world: World):
    """Добрать места можно, пока они есть."""
    screening = await world.screening(capacity=4)
    booking = await world.bookings.book(GUEST, screening.id, 1)
    await world.bookings.book(OTHER_GUEST, screening.id, 2)

    changed = await world.bookings.change_seats(GUEST, booking.id, 2)
    with pytest.raises(NotEnoughSeatsError):
        await world.bookings.change_seats(GUEST, booking.id, 3)

    assert changed.seats == 2
    assert world.db.screenings[screening.id].seats_taken == 4


async def test_decrease_seats_returns_them(world: World):
    """Лишние места возвращаются хосту."""
    screening = await world.screening(capacity=6)
    booking = await world.bookings.book(GUEST, screening.id, 5)

    await world.bookings.change_seats(GUEST, booking.id, 2)

    assert world.db.screenings[screening.id].seats_taken == 2


async def test_same_seats_is_nothing_to_change(world: World):
    """То же число мест — понятный отказ, а не пустое событие хосту."""
    screening = await world.screening()
    booking = await world.bookings.book(GUEST, screening.id, 2)

    with pytest.raises(NothingToChangeError):
        await world.bookings.change_seats(GUEST, booking.id, 2)


async def test_cancel_returns_seats_and_notifies_host(world: World):
    """Отмена брони возвращает места и пишет хосту; гостю — нет, он сам отменил."""
    screening = await world.screening(capacity=6)
    booking = await world.bookings.book(GUEST, screening.id, 3)
    before = len(world.outbox.payloads)

    cancelled = await world.bookings.cancel(GUEST, booking.id)

    assert cancelled.status is BookingStatus.CANCELLED
    assert world.db.screenings[screening.id].seats_taken == 0
    new_events = world.outbox.payloads[before:]
    assert [(e['template_code'], e['context']['change']) for e in new_events] == [('booking_host_update', 'cancelled')]


async def test_cancelled_booking_frees_guest_to_book_again(world: World):
    """После отмены гость может забронировать заново: уникальна только активная бронь."""
    screening = await world.screening()
    booking = await world.bookings.book(GUEST, screening.id, 1)
    await world.bookings.cancel(GUEST, booking.id)

    again = await world.bookings.book(GUEST, screening.id, 2)

    assert again.id != booking.id


async def test_only_owner_changes_booking(world: World):
    """Чужую бронь не изменить и не отменить."""
    screening = await world.screening()
    booking = await world.bookings.book(GUEST, screening.id, 1)

    with pytest.raises(NotBookingOwnerError):
        await world.bookings.cancel(OTHER_GUEST, booking.id)
    with pytest.raises(NotBookingOwnerError):
        await world.bookings.change_seats(HOST, booking.id, 2)


async def test_cancelled_booking_is_final(world: World):
    """Отменённую бронь не меняют и не отменяют повторно."""
    screening = await world.screening()
    booking = await world.bookings.book(GUEST, screening.id, 1)
    await world.bookings.cancel(GUEST, booking.id)

    with pytest.raises(BookingCancelledError):
        await world.bookings.cancel(GUEST, booking.id)
    with pytest.raises(BookingCancelledError):
        await world.bookings.change_seats(GUEST, booking.id, 2)


async def test_unknown_booking(world: World):
    """Несуществующая бронь — 404."""
    with pytest.raises(BookingNotFoundError):
        await world.bookings.cancel(GUEST, uuid4())


async def test_booking_after_start_cannot_be_cancelled(world: World):
    """После начала показа бронь уже не отменить: места не вернуть задним числом."""
    screening = await world.screening(starts_in=timedelta(hours=1))
    booking = await world.bookings.book(GUEST, screening.id, 1)
    world.clock.advance(timedelta(hours=2))

    with pytest.raises(ScreeningClosedError):
        await world.bookings.cancel(GUEST, booking.id)
    assert world.db.bookings[booking.id].status is BookingStatus.ACTIVE


async def test_guest_list_is_for_host_only(world: World):
    """Список гостей с рейтингом видит только хост показа."""
    screening = await world.screening()
    await world.bookings.book(GUEST, screening.id, 2)

    guests = await world.bookings.guests(HOST, screening.id)
    with pytest.raises(NotScreeningHostError):
        await world.bookings.guests(GUEST, screening.id)

    assert [(entry.booking.guest_name, entry.booking.seats) for entry in guests] == [('Тринити', 2)]


async def test_my_bookings_split_by_period(world: World):
    """«Мои брони» делятся на будущие и прошедшие и несут показ целиком."""
    soon = await world.screening(starts_in=timedelta(hours=1))
    later = await world.screening(starts_in=timedelta(days=3))
    await world.bookings.book(GUEST, soon.id, 1)
    await world.bookings.book(GUEST, later.id, 1)
    world.clock.advance(timedelta(hours=2))

    upcoming = await world.bookings.of_guest(GUEST, Period.UPCOMING, PAGE)
    past = await world.bookings.of_guest(GUEST, Period.PAST, PAGE)

    assert [view.screening.id for view in upcoming.items] == [later.id]
    assert [view.screening.id for view in past.items] == [soon.id]
