"""Хранилища и соседние сервисы в памяти — для unit-тестов бизнес-логики.

Все репозитории делят одну `Database`, как в бою все репозитории запроса
делят одну сессию. Единица работы запоминает состояние базы при каждой
фиксации и возвращает его при откате: так тест видит то же, что увидел бы в PostgreSQL, —
занятые условным UPDATE места откатываются вместе с несостоявшейся бронью.
"""

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from models.domain import (
    Booking,
    BookingDraft,
    BookingStatus,
    BookingView,
    Film,
    GuestEntry,
    HostOffer,
    OutboxDraft,
    OutboxMessage,
    Page,
    PageRequest,
    Period,
    Rating,
    RatingDraft,
    RatingSummary,
    Role,
    Screening,
    ScreeningChanges,
    ScreeningDraft,
    ScreeningStatus,
    UserRating,
)
from storage.base import (
    AlreadyExistsError,
    BookingRepository,
    Catalog,
    NotificationGateway,
    Outbox,
    People,
    RatingRepository,
    ScreeningRepository,
    Sessions,
    StorageUnavailableError,
    UnitOfWork,
)


@dataclass
class OutboxEntry:
    id: UUID
    payload: dict[str, Any]
    request_id: str
    available_at: datetime
    attempts: int = 0
    last_error: str | None = None


@dataclass
class Database:
    screenings: dict[UUID, Screening] = field(default_factory=dict)
    bookings: dict[UUID, Booking] = field(default_factory=dict)
    ratings: dict[UUID, Rating] = field(default_factory=dict)
    # (user_id, role) → (сумма оценок, число оценок)
    user_ratings: dict[tuple[UUID, Role], tuple[int, int]] = field(default_factory=dict)
    outbox: dict[UUID, OutboxEntry] = field(default_factory=dict)
    commits: int = 0
    rollbacks: int = 0
    # Строки, которые транзакция заблокировала, в порядке блокировки: явная
    # блокировка (lock=True) и любое изменение строки. Порядок проверяют тесты
    # взаимных блокировок: показ всегда раньше брони.
    locks: list[tuple[str, UUID]] = field(default_factory=list)
    # Последнее зафиксированное состояние — общее для всех единиц работы над
    # этой базой, как общая для всех транзакций база в PostgreSQL.
    committed: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.committed = self.state()

    def state(self) -> dict[str, Any]:
        return {
            name: copy.deepcopy(getattr(self, name))
            for name in ('screenings', 'bookings', 'ratings', 'user_ratings', 'outbox')
        }

    def restore(self, state: dict[str, Any]) -> None:
        for name, value in state.items():
            setattr(self, name, copy.deepcopy(value))


def now() -> datetime:
    return datetime.now(UTC)


def _page[T](items: list[T], page: PageRequest) -> Page[T]:
    return Page(
        items=items[page.offset:page.offset + page.page_size], total=len(items),
        page_number=page.page_number, page_size=page.page_size,
    )


def _summary(value: tuple[int, int] | None) -> RatingSummary:
    if not value or not value[1]:
        return RatingSummary()
    return RatingSummary(average=round(value[0] / value[1], 2), votes=value[1])


class FakeUnitOfWork(UnitOfWork):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def commit(self) -> None:
        self.db.commits += 1
        self.db.committed = self.db.state()

    async def rollback(self) -> None:
        self.db.rollbacks += 1
        self.db.restore(self.db.committed)


class FakeScreeningRepository(ScreeningRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, draft: ScreeningDraft) -> Screening:
        moment = now()
        screening = Screening(
            id=uuid4(), seats_taken=0, status=ScreeningStatus.SCHEDULED, created_at=moment, updated_at=moment,
            **draft.model_dump(),
        )
        self.db.screenings[screening.id] = screening
        return screening

    async def get(self, screening_id: UUID, *, lock: bool = False) -> Screening | None:
        if lock:
            self.db.locks.append(('screening', screening_id))
        return self.db.screenings.get(screening_id)

    async def update(self, screening_id: UUID, changes: ScreeningChanges) -> Screening | None:
        screening = self.db.screenings[screening_id]
        if changes.capacity is not None and changes.capacity < screening.seats_taken:
            return None
        return self._save(screening, **changes.model_dump(exclude_none=True))

    async def take_seats(self, screening_id: UUID, seats: int, now: datetime) -> Screening | None:
        self.db.locks.append(('screening', screening_id))
        screening = self.db.screenings.get(screening_id)
        if screening is None or not screening.is_open(now) or screening.seats_left < seats:
            return None
        return self._save(screening, seats_taken=screening.seats_taken + seats)

    async def release_seats(self, screening_id: UUID, seats: int) -> None:
        self.db.locks.append(('screening', screening_id))
        screening = self.db.screenings[screening_id]
        self._save(screening, seats_taken=screening.seats_taken - seats)

    async def cancel(self, screening_id: UUID) -> None:
        self.db.locks.append(('screening', screening_id))
        self._save(self.db.screenings[screening_id], status=ScreeningStatus.CANCELLED)

    async def upcoming(
        self, now: datetime, page: PageRequest, film_id: UUID | None = None, host_id: UUID | None = None,
    ) -> Page[Screening]:
        items = sorted(
            (
                s for s in self.db.screenings.values()
                if s.is_open(now) and film_id in (None, s.film_id) and host_id in (None, s.host_id)
            ),
            key=lambda s: s.starts_at,
        )
        return _page(items, page)

    async def of_host(self, host_id: UUID, period: Period, now: datetime, page: PageRequest) -> Page[Screening]:
        upcoming = period is Period.UPCOMING
        items = sorted(
            (s for s in self.db.screenings.values() if s.host_id == host_id and (s.starts_at > now) == upcoming),
            key=lambda s: s.starts_at, reverse=not upcoming,
        )
        return _page(items, page)

    async def hosts_of_film(self, film_id: UUID, now: datetime, page: PageRequest) -> Page[HostOffer]:
        by_host: dict[UUID, list[Screening]] = {}
        for screening in self.db.screenings.values():
            if screening.film_id == film_id and screening.is_open(now):
                by_host.setdefault(screening.host_id, []).append(screening)
        offers = [
            HostOffer(
                host_id=host_id,
                host_name=max(screenings, key=lambda s: s.created_at).host_name,
                screenings=len(screenings),
                next_starts_at=min(s.starts_at for s in screenings),
                seats_left=sum(s.seats_left for s in screenings),
                rating=_summary(self.db.user_ratings.get((host_id, Role.HOST))),
            )
            for host_id, screenings in by_host.items()
        ]
        return _page(sorted(offers, key=lambda offer: offer.next_starts_at), page)

    def _save(self, screening: Screening, **values: Any) -> Screening:
        updated = screening.model_copy(update={**values, 'updated_at': now()})
        self.db.screenings[screening.id] = updated
        return updated


class FakeBookingRepository(BookingRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, draft: BookingDraft) -> Booking:
        # Уникальный индекс базы — не через active_of: тест может подменить
        # раннюю проверку, а индекс должен сработать всё равно.
        if self._active(draft.screening_id, draft.guest_id) is not None:
            raise AlreadyExistsError('uq_bookings_active_guest')
        moment = now()
        booking = Booking(
            id=uuid4(), status=BookingStatus.ACTIVE, created_at=moment, updated_at=moment, **draft.model_dump(),
        )
        self.db.bookings[booking.id] = booking
        return booking

    async def get(self, booking_id: UUID, *, lock: bool = False) -> Booking | None:
        if lock:
            self.db.locks.append(('booking', booking_id))
        return self.db.bookings.get(booking_id)

    async def active_of(self, screening_id: UUID, guest_id: UUID) -> Booking | None:
        return self._active(screening_id, guest_id)

    def _active(self, screening_id: UUID, guest_id: UUID) -> Booking | None:
        return next(
            (
                b for b in self.db.bookings.values()
                if b.screening_id == screening_id and b.guest_id == guest_id and b.status is BookingStatus.ACTIVE
            ),
            None,
        )

    async def change_seats(self, booking_id: UUID, seats: int) -> Booking:
        return self._save(booking_id, seats=seats)

    async def cancel(self, booking_id: UUID) -> Booking:
        return self._save(booking_id, status=BookingStatus.CANCELLED)

    async def cancel_all(self, screening_id: UUID) -> list[Booking]:
        return [
            self._save(b.id, status=BookingStatus.CANCELLED)
            for b in list(self.db.bookings.values())
            if b.screening_id == screening_id and b.status is BookingStatus.ACTIVE
        ]

    async def guests(self, screening_id: UUID) -> list[GuestEntry]:
        return [
            GuestEntry(booking=b, rating=_summary(self.db.user_ratings.get((b.guest_id, Role.GUEST))))
            for b in sorted(self.db.bookings.values(), key=lambda b: b.created_at)
            if b.screening_id == screening_id and b.status is BookingStatus.ACTIVE
        ]

    async def of_guest(self, guest_id: UUID, period: Period, now: datetime, page: PageRequest) -> Page[BookingView]:
        upcoming = period is Period.UPCOMING
        views = [
            BookingView(booking=b, screening=self.db.screenings[b.screening_id])
            for b in self.db.bookings.values()
            if b.guest_id == guest_id and (self.db.screenings[b.screening_id].starts_at > now) == upcoming
        ]
        views.sort(key=lambda view: view.screening.starts_at, reverse=not upcoming)
        return _page(views, page)

    def _save(self, booking_id: UUID, **values: Any) -> Booking:
        self.db.locks.append(('booking', booking_id))
        updated = self.db.bookings[booking_id].model_copy(update={**values, 'updated_at': now()})
        self.db.bookings[booking_id] = updated
        return updated


class FakeRatingRepository(RatingRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, draft: RatingDraft) -> Rating:
        for rating in self.db.ratings.values():
            if (rating.screening_id, rating.author_id, rating.target_id) == (
                draft.screening_id, draft.author_id, draft.target_id,
            ):
                raise AlreadyExistsError('uq_ratings_pair')
        rating = Rating(id=uuid4(), created_at=now(), **draft.model_dump())
        self.db.ratings[rating.id] = rating
        total, votes = self.db.user_ratings.get((draft.target_id, draft.target_role), (0, 0))
        self.db.user_ratings[(draft.target_id, draft.target_role)] = (total + draft.score, votes + 1)
        return rating

    async def by_author(self, screening_id: UUID, author_id: UUID) -> list[Rating]:
        return [r for r in self.db.ratings.values() if r.screening_id == screening_id and r.author_id == author_id]

    async def received(self, user_id: UUID, role: Role, page: PageRequest) -> Page[Rating]:
        items = sorted(
            (r for r in self.db.ratings.values() if r.target_id == user_id and r.target_role is role),
            key=lambda r: r.created_at, reverse=True,
        )
        return _page(items, page)

    async def summary(self, user_id: UUID) -> UserRating:
        return UserRating(
            user_id=user_id,
            as_host=_summary(self.db.user_ratings.get((user_id, Role.HOST))),
            as_guest=_summary(self.db.user_ratings.get((user_id, Role.GUEST))),
        )


class FakeOutbox(Outbox):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, drafts: Sequence[OutboxDraft]) -> None:
        for draft in drafts:
            entry = OutboxEntry(id=uuid4(), payload=draft.payload, request_id=draft.request_id, available_at=now())
            self.db.outbox[entry.id] = entry

    async def claim(self, limit: int, lease: timedelta, now: datetime) -> list[OutboxMessage]:
        due = sorted((e for e in self.db.outbox.values() if e.available_at <= now), key=lambda e: e.available_at)
        claimed = []
        for entry in due[:limit]:
            entry.available_at = now + lease
            entry.attempts += 1
            claimed.append(OutboxMessage(
                id=entry.id, payload=entry.payload, request_id=entry.request_id, attempts=entry.attempts,
            ))
        return claimed

    async def done(self, message_id: UUID) -> None:
        self.db.outbox.pop(message_id, None)

    async def retry(self, message_id: UUID, at: datetime, error: str) -> None:
        entry = self.db.outbox[message_id]
        entry.available_at, entry.last_error = at, error

    @property
    def payloads(self) -> list[dict[str, Any]]:
        return [entry.payload for entry in self.db.outbox.values()]


class FakeCatalog(Catalog):
    def __init__(self, films: Sequence[Film] = (), available: bool = True) -> None:
        self.films = {film.id: film for film in films}
        self.available = available
        self.authorizations: list[str | None] = []

    async def film(self, film_id: UUID, authorization: str | None) -> Film | None:
        self.authorizations.append(authorization)
        if not self.available:
            raise StorageUnavailableError('Async API недоступен')
        return self.films.get(film_id)


class FakePeople(People):
    def __init__(self, names: dict[UUID, str] | None = None, available: bool = True) -> None:
        self.known = names or {}
        self.available = available

    async def names(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        if not self.available:
            raise StorageUnavailableError('Сервис авторизации недоступен')
        return {user_id: self.known[user_id] for user_id in user_ids if user_id in self.known}


class FakeSessions(Sessions):
    """Сессии сервиса авторизации: закрытые — по заголовку Authorization."""

    def __init__(self) -> None:
        self.closed: set[str] = set()
        self.available = True
        self.checked: list[str] = []

    async def is_active(self, authorization: str) -> bool:
        self.checked.append(authorization)
        if not self.available:
            raise StorageUnavailableError('Сервис авторизации недоступен')
        return authorization not in self.closed


class FakeGateway(NotificationGateway):
    """Сервис уведомлений: запоминает присланное или отвечает заданным отказом."""

    def __init__(self) -> None:
        self.sent: list[tuple[UUID, dict, str]] = []
        self.failure: Exception | None = None

    async def send(self, event_id: UUID, payload: dict, request_id: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.sent.append((event_id, payload, request_id))
