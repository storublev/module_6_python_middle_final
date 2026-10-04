"""Показы: кто и когда может их создать, изменить и отменить."""

from datetime import timedelta
from uuid import uuid4

import pytest

from models.domain import BookingStatus, Period, ScreeningChanges, ScreeningStatus
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
from services.screenings import NewScreening
from storage.base import StorageUnavailableError
from tests.unit.conftest import GUEST, HOST, MOVIE, OTHER_GUEST, PAGE, SERIES, World


def new_screening(world: World, **overrides) -> NewScreening:
    data = {
        'film_id': MOVIE.id, 'starts_at': world.clock() + timedelta(days=1), 'place': 'Зал 3',
        'address': 'Новый Арбат, 24', 'description': 'Обсуждение после фильма', 'capacity': 6,
    }
    return NewScreening(**(data | overrides))


async def test_host_creates_screening_with_film_snapshot(world: World):
    """Показ получает снимок названия и обложки фильма и имени хоста — списки не ходят за ними в соседей."""
    screening = await world.screenings.create(HOST, new_screening(world), 'Bearer abc')

    assert (screening.film_title, screening.film_poster, screening.host_name) == (
        MOVIE.title, MOVIE.poster_url, 'Нео Андерсон',
    )
    assert (screening.seats_taken, screening.seats_left, screening.status) == (0, 6, ScreeningStatus.SCHEDULED)
    assert world.catalog.authorizations == ['Bearer abc']


async def test_series_cannot_be_screened(world: World):
    """Кнопка «Купить билет» — только у полнометражных фильмов: на сериал показ не создать."""
    with pytest.raises(FilmNotBookableError):
        await world.screenings.create(HOST, new_screening(world, film_id=SERIES.id), None)


async def test_unknown_film(world: World):
    """Фильм, которого нет в каталоге (или он закрыт подпиской), — 404 film_not_found."""
    with pytest.raises(FilmNotFoundError):
        await world.screenings.create(HOST, new_screening(world, film_id=uuid4()), None)


@pytest.mark.parametrize(
    'starts_in, error',
    [
        pytest.param(timedelta(minutes=-5), StartsTooSoonError, id='past'),
        pytest.param(timedelta(minutes=29), StartsTooSoonError, id='too-soon'),
        pytest.param(timedelta(days=366), StartsTooLateError, id='too-far'),
    ],
)
async def test_start_time_window(world: World, starts_in, error):
    """Показ — не в прошлом, не раньше чем через 30 минут и не дальше года."""
    with pytest.raises(error):
        await world.screenings.create(HOST, new_screening(world, starts_at=world.clock() + starts_in), None)


@pytest.mark.parametrize('starts_in', [timedelta(minutes=30), timedelta(days=365)], ids=['earliest', 'latest'])
async def test_start_time_window_edges_are_allowed(world: World, starts_in):
    """Границы окна включены: ровно через 30 минут и ровно через год — можно."""
    screening = await world.screenings.create(HOST, new_screening(world, starts_at=world.clock() + starts_in), None)

    assert screening.starts_at == world.clock() + starts_in


@pytest.mark.parametrize('capacity', [0, 51])
async def test_capacity_range(world: World, capacity):
    """Мест от 1 до 50: посиделки камерные."""
    with pytest.raises(CapacityOutOfRangeError):
        await world.screenings.create(HOST, new_screening(world, capacity=capacity), None)


async def test_catalog_outage_is_storage_error(world: World):
    """Каталог молчит — показ не создаётся, наружу уходит сбой хранилища (503), а не 500."""
    world.catalog.available = False

    with pytest.raises(StorageUnavailableError):
        await world.screenings.create(HOST, new_screening(world), None)
    assert world.db.screenings == {}


async def test_name_service_outage_does_not_block_screening(world: World):
    """Справочник имён молчит — показ всё равно создаётся, хост подписан «Зритель»."""
    world.people.available = False

    screening = await world.screenings.create(HOST, new_screening(world), None)

    assert screening.host_name == 'Зритель'


async def test_only_host_updates(world: World):
    """Чужой показ изменить нельзя."""
    screening = await world.screening()

    with pytest.raises(NotScreeningHostError):
        await world.screenings.update(GUEST, screening.id, ScreeningChanges(place='Другой зал'))


async def test_update_unknown_screening(world: World):
    """Несуществующий показ — 404."""
    with pytest.raises(ScreeningNotFoundError):
        await world.screenings.update(HOST, uuid4(), ScreeningChanges(place='Зал'))


async def test_empty_update_is_rejected(world: World):
    """Запрос без полей ничего не меняет и отвечает понятной ошибкой, а не тихим успехом."""
    screening = await world.screening()

    with pytest.raises(NothingToChangeError):
        await world.screenings.update(HOST, screening.id, ScreeningChanges())


async def test_capacity_cannot_drop_below_booked(world: World):
    """Мест нельзя сделать меньше, чем уже забронировано (ФТ-8)."""
    screening = await world.screening(capacity=6)
    await world.bookings.book(GUEST, screening.id, 4)

    with pytest.raises(CapacityBelowBookedError):
        await world.screenings.update(HOST, screening.id, ScreeningChanges(capacity=3))
    updated = await world.screenings.update(HOST, screening.id, ScreeningChanges(capacity=4))
    assert (updated.capacity, updated.seats_left) == (4, 0)


async def test_new_time_is_checked_too(world: World):
    """Перенос показа подчиняется тем же правилам времени, что и создание."""
    screening = await world.screening()

    with pytest.raises(StartsTooSoonError):
        await world.screenings.update(HOST, screening.id, ScreeningChanges(starts_at=world.clock()))


async def test_moving_screening_notifies_guests(world: World):
    """О переносе времени или места пишут всем гостям одним событием."""
    screening = await world.screening()
    await world.bookings.book(GUEST, screening.id, 1)
    await world.bookings.book(OTHER_GUEST, screening.id, 2)
    before = len(world.outbox.payloads)

    await world.screenings.update(HOST, screening.id, ScreeningChanges(place='Зал 5'))

    event = world.outbox.payloads[before]
    assert event['routing_key'] == 'screening-reporting.v1.changed'
    assert sorted(event['audience']['user_ids']) == sorted([str(GUEST), str(OTHER_GUEST)])
    assert event['context']['place'] == 'Зал 5'


async def test_description_change_does_not_spam_guests(world: World):
    """Правка описания не повод писать гостям: они придут туда же и тогда же."""
    screening = await world.screening()
    await world.bookings.book(GUEST, screening.id, 1)
    before = len(world.outbox.payloads)

    await world.screenings.update(HOST, screening.id, ScreeningChanges(description='Берите попкорн'))

    assert len(world.outbox.payloads) == before


async def test_cancel_cancels_all_bookings_and_notifies(world: World):
    """Отмена показа отменяет все брони и пишет гостям (ФТ-9)."""
    screening = await world.screening()
    first = await world.bookings.book(GUEST, screening.id, 1)
    second = await world.bookings.book(OTHER_GUEST, screening.id, 2)

    cancelled = await world.screenings.cancel(HOST, screening.id)

    assert cancelled.status is ScreeningStatus.CANCELLED
    assert {world.db.bookings[b.id].status for b in (first, second)} == {BookingStatus.CANCELLED}
    assert world.outbox.payloads[-1]['routing_key'] == 'screening-reporting.v1.cancelled'


async def test_started_screening_is_frozen(world: World):
    """Начавшийся показ не меняют и не отменяют."""
    screening = await world.screening(starts_in=timedelta(hours=1))
    world.clock.advance(timedelta(hours=2))

    with pytest.raises(ScreeningClosedError):
        await world.screenings.cancel(HOST, screening.id)
    with pytest.raises(ScreeningClosedError):
        await world.screenings.update(HOST, screening.id, ScreeningChanges(place='Зал'))


async def test_cancelled_screening_cannot_be_cancelled_again(world: World):
    """Повторная отмена — 409, а не второе письмо гостям."""
    screening = await world.screening()
    await world.screenings.cancel(HOST, screening.id)

    with pytest.raises(ScreeningClosedError):
        await world.screenings.cancel(HOST, screening.id)


async def test_hosts_of_film_and_their_dates(world: World):
    """Карточка фильма: хосты с ближайшей датой и рейтингом, затем — даты выбранного хоста."""
    first = await world.screening(starts_in=timedelta(days=3))
    await world.screening(starts_in=timedelta(days=1))
    await world.screening(starts_in=timedelta(days=2), host=OTHER_GUEST)

    hosts = await world.screenings.hosts_of_film(MOVIE.id, PAGE)
    dates = await world.screenings.upcoming(PAGE, film_id=MOVIE.id, host_id=HOST)

    assert [offer.host_id for offer in hosts.items] == [HOST, OTHER_GUEST]
    assert (hosts.items[0].screenings, hosts.items[0].seats_left) == (2, 12)
    assert [s.starts_at for s in dates.items] == sorted(s.starts_at for s in dates.items)
    assert first.id in {s.id for s in dates.items}


async def test_cancelled_and_past_screenings_are_not_offered(world: World):
    """В карточке фильма нет отменённых и прошедших показов — на них не забронировать."""
    cancelled = await world.screening()
    await world.screenings.cancel(HOST, cancelled.id)
    await world.screening(starts_in=timedelta(hours=1))
    world.clock.advance(timedelta(hours=2))

    assert (await world.screenings.hosts_of_film(MOVIE.id, PAGE)).total == 0


async def test_host_schedule_split_by_period(world: World):
    """Расписание хоста делится на будущие и прошедшие показы (ФТ-7)."""
    past = await world.screening(starts_in=timedelta(hours=1))
    future = await world.screening(starts_in=timedelta(days=5))
    world.clock.advance(timedelta(hours=2))

    upcoming = await world.screenings.of_host(HOST, Period.UPCOMING, PAGE)
    history = await world.screenings.of_host(HOST, Period.PAST, PAGE)

    assert [s.id for s in upcoming.items] == [future.id]
    assert [s.id for s in history.items] == [past.id]
