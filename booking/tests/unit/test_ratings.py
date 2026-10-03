"""Оценки хоста и гостя — задание со звёздочкой (ФТ-17…ФТ-19)."""

from datetime import timedelta
from uuid import uuid4

import pytest

from models.domain import Role
from services.errors import (
    AlreadyRatedError,
    InvalidRatingTargetError,
    NotParticipantError,
    RatingTooEarlyError,
    ScreeningCancelledError,
    ScreeningNotFoundError,
)
from tests.unit.conftest import GUEST, HOST, MOVIE, OTHER_GUEST, PAGE, World


async def finished_screening(world: World, *guests):
    """Показ, на который пришли гости и который уже начался."""
    screening = await world.screening(starts_in=timedelta(hours=1))
    for guest in guests:
        await world.bookings.book(guest, screening.id, 1)
    world.clock.advance(timedelta(hours=2))
    return screening


async def test_guest_rates_host(world: World):
    """Гость оценивает хоста; оценка двигает рейтинг хоста как хоста."""
    screening = await finished_screening(world, GUEST, OTHER_GUEST)

    await world.ratings.rate(GUEST, screening.id, HOST, 5, 'Отличная компания')
    rating = await world.ratings.rate(OTHER_GUEST, screening.id, HOST, 4, None)

    summary = await world.ratings.summary(HOST)
    assert (rating.target_role, rating.author_name) == (Role.HOST, 'Морфеус')
    assert (summary.as_host.average, summary.as_host.votes, summary.as_guest.votes) == (4.5, 2, 0)


async def test_host_rates_guest(world: World):
    """Хост оценивает гостя; оценка двигает рейтинг гостя как гостя."""
    screening = await finished_screening(world, GUEST)

    rating = await world.ratings.rate(HOST, screening.id, GUEST, 3, 'Опоздал на полчаса')

    summary = await world.ratings.summary(GUEST)
    assert (rating.target_role, rating.author_name) == (Role.GUEST, 'Нео Андерсон')
    assert (summary.as_guest.average, summary.as_guest.votes) == (3.0, 1)


async def test_rating_before_start_is_too_early(world: World):
    """До начала показа оценивать нечего."""
    screening = await world.screening()
    await world.bookings.book(GUEST, screening.id, 1)

    with pytest.raises(RatingTooEarlyError):
        await world.ratings.rate(GUEST, screening.id, HOST, 5, None)


async def test_outsider_cannot_rate(world: World):
    """Посторонний — не гость и не хост — оценить никого не может."""
    screening = await finished_screening(world, GUEST)

    with pytest.raises(NotParticipantError):
        await world.ratings.rate(OTHER_GUEST, screening.id, HOST, 1, 'Накрутка')


async def test_guest_rates_only_host(world: World):
    """Гость оценивает только хоста, а не других гостей."""
    screening = await finished_screening(world, GUEST, OTHER_GUEST)

    with pytest.raises(InvalidRatingTargetError):
        await world.ratings.rate(GUEST, screening.id, OTHER_GUEST, 5, None)


async def test_host_rates_only_own_guests(world: World):
    """Хост не оценит того, кто не бронировал места на этом показе."""
    screening = await finished_screening(world, GUEST)

    with pytest.raises(InvalidRatingTargetError):
        await world.ratings.rate(HOST, screening.id, OTHER_GUEST, 1, None)


async def test_cancelled_booking_is_not_participation(world: World):
    """Гость, отменивший бронь, на показ не пришёл и оценивать не может."""
    screening = await world.screening(starts_in=timedelta(hours=1))
    booking = await world.bookings.book(GUEST, screening.id, 1)
    await world.bookings.cancel(GUEST, booking.id)
    world.clock.advance(timedelta(hours=2))

    with pytest.raises(NotParticipantError):
        await world.ratings.rate(GUEST, screening.id, HOST, 5, None)


async def test_one_rating_per_pair(world: World):
    """Одна оценка на пару за показ: вторая — 409, рейтинг не сдвигается."""
    screening = await finished_screening(world, GUEST)
    await world.ratings.rate(GUEST, screening.id, HOST, 5, None)

    with pytest.raises(AlreadyRatedError):
        await world.ratings.rate(GUEST, screening.id, HOST, 1, None)
    assert (await world.ratings.summary(HOST)).as_host.votes == 1


async def test_cancelled_screening_cannot_be_rated(world: World):
    """Отменённый показ не состоялся — оценивать нечего."""
    screening = await world.screening(starts_in=timedelta(hours=1))
    await world.bookings.book(GUEST, screening.id, 1)
    await world.screenings.cancel(HOST, screening.id)
    world.clock.advance(timedelta(hours=2))

    with pytest.raises(ScreeningCancelledError):
        await world.ratings.rate(GUEST, screening.id, HOST, 5, None)


async def test_unknown_screening(world: World):
    """Оценка несуществующего показа — 404."""
    with pytest.raises(ScreeningNotFoundError):
        await world.ratings.rate(GUEST, uuid4(), HOST, 5, None)


async def test_mine_and_received(world: World):
    """«Кого я уже оценил» на показе и отзывы о зрителе в одной роли."""
    screening = await finished_screening(world, GUEST, OTHER_GUEST)
    await world.ratings.rate(HOST, screening.id, GUEST, 5, 'Пришла вовремя')
    await world.ratings.rate(GUEST, screening.id, HOST, 4, 'Уютно')

    mine = await world.ratings.mine(HOST, screening.id)
    reviews = await world.ratings.received(HOST, Role.HOST, PAGE)

    assert [rating.target_id for rating in mine] == [GUEST]
    assert [(r.author_name, r.comment) for r in reviews.items] == [('Тринити', 'Уютно')]


async def test_host_rating_is_shown_when_choosing_host(world: World):
    """Рейтинг хоста виден в блоке «Кто показывает» карточки фильма (ФТ-19)."""
    screening = await finished_screening(world, GUEST)
    await world.ratings.rate(GUEST, screening.id, HOST, 4, None)
    await world.screening(starts_in=timedelta(days=1))

    offers = await world.screenings.hosts_of_film(MOVIE.id, PAGE)

    assert (offers.items[0].rating.average, offers.items[0].rating.votes) == (4.0, 1)


async def test_unrated_user_has_empty_rating(world: World):
    """У того, кого не оценивали, — пустой рейтинг, а не ошибка."""
    summary = await world.ratings.summary(uuid4())

    assert (summary.as_host.average, summary.as_host.votes) == (None, 0)
