"""Сервисы на хранилищах в памяти и управляемые часы."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from models.domain import Film, PageRequest, Screening
from services.bookings import BookingService
from services.letters import Letters
from services.people import NameResolver
from services.ratings import RatingService
from services.screenings import NewScreening, ScreeningRules, ScreeningService
from tests.unit.fakes import (
    Database,
    FakeBookingRepository,
    FakeCatalog,
    FakeOutbox,
    FakePeople,
    FakeRatingRepository,
    FakeScreeningRepository,
    FakeUnitOfWork,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
HOST = UUID('6f0c5c9c-4a55-4f6f-8f2a-2f1a5a8b0e11')
GUEST = UUID('c4b1a2d3-5e6f-4a7b-8c9d-0e1f2a3b4c5d')
OTHER_GUEST = UUID('0d6e2b3a-9a1c-4c7e-8b5f-3e2d1c0b9a87')
MOVIE = Film(id=uuid4(), title='Star Wars', type='movie', poster_url='https://e.com/sw.jpg')
SERIES = Film(id=uuid4(), title='Star Trek', type='tv_show')
RULES = ScreeningRules(
    min_capacity=1, max_capacity=50, min_lead_time=timedelta(minutes=30), max_lead_time=timedelta(days=365),
)
MAX_SEATS = 10
BASE_URL = 'https://practix.local'
PAGE = PageRequest(page_number=1, page_size=20)


class Clock:
    """Часы, которые тест переводит сам: «показ начался» без ожидания."""

    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, delta: timedelta) -> None:
        self.moment += delta


@dataclass
class World:
    """Всё, что нужно тесту: база в памяти, соседи, часы и сервисы поверх них."""

    db: Database
    clock: Clock
    catalog: FakeCatalog
    people: FakePeople
    outbox: FakeOutbox
    screenings: ScreeningService
    bookings: BookingService
    ratings: RatingService

    async def screening(
        self, capacity: int = 6, starts_in: timedelta = timedelta(days=2), host: UUID = HOST,
    ) -> Screening:
        """Показ хоста на фильм MOVIE через `starts_in` от текущего времени."""
        data = NewScreening(
            film_id=MOVIE.id, starts_at=self.clock() + starts_in, place='Кинотеатр «Октябрь», зал 3',
            address='Москва, Новый Арбат, 24', description=None, capacity=capacity,
        )
        return await self.screenings.create(host, data, authorization='Bearer token')


@pytest.fixture
def world() -> World:
    db = Database()
    clock = Clock()
    catalog = FakeCatalog([MOVIE, SERIES])
    people = FakePeople({HOST: 'Нео Андерсон', GUEST: 'Тринити', OTHER_GUEST: 'Морфеус'})
    letters = Letters(BASE_URL, 'Europe/Moscow')
    names = NameResolver(people)
    screenings = FakeScreeningRepository(db)
    bookings = FakeBookingRepository(db)
    outbox = FakeOutbox(db)
    return World(
        db=db,
        clock=clock,
        catalog=catalog,
        people=people,
        outbox=outbox,
        screenings=ScreeningService(
            FakeUnitOfWork(db), screenings, bookings, outbox, catalog, names, letters, RULES, clock,
        ),
        bookings=BookingService(FakeUnitOfWork(db), screenings, bookings, outbox, names, letters, MAX_SEATS, clock),
        ratings=RatingService(FakeUnitOfWork(db), screenings, bookings, FakeRatingRepository(db), clock),
    )
