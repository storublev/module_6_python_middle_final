"""Реализации хранилищ на PostgreSQL.

Все репозитории одного запроса работают в одной сессии SQLAlchemy, а
фиксирует её единица работы (`PostgresUnitOfWork`): бронь, счётчик мест и
событие для писем попадают в базу одной транзакцией.

Сбой базы выходит наружу как `StorageUnavailableError`: исключения SQLAlchemy
и asyncpg за пределы этого модуля не уходят — иначе бизнес-логика узнала бы,
какая под ней база.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import (
    Booking,
    BookingDraft,
    BookingStatus,
    BookingView,
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
    RejectedEvent,
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
    Outbox,
    RatingRepository,
    ScreeningRepository,
    StorageUnavailableError,
    UnitOfWork,
)
from storage.orm import BookingRow, OutboxRow, RatingRow, ScreeningRow, UserRatingRow

logger = logging.getLogger(__name__)

SCHEDULED = ScreeningStatus.SCHEDULED.value
ACTIVE = BookingStatus.ACTIVE.value
# Строка, прочитанная повторно в той же сессии, должна прийти свежей из базы,
# а не из карты идентичности SQLAlchemy: между чтениями её мог поменять
# условный UPDATE или соседняя транзакция.
FRESH = {'populate_existing': True}


class PostgresRepository:
    """Общая часть: сессия и перевод сбоев базы в контракт хранилища."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def _errors(self) -> AsyncIterator[None]:
        try:
            yield
        except (OperationalError, InterfaceError, OSError, TimeoutError) as error:
            raise StorageUnavailableError(str(error)) from error

    async def _page(self, query: Select, page: PageRequest, convert: Any) -> Page:
        """Страница выдачи и общее число записей одним походом за каждым."""
        total_query = select(func.count()).select_from(query.order_by(None).subquery())
        async with self._errors():
            total = await self.session.scalar(total_query) or 0
            rows = (await self.session.execute(query.offset(page.offset).limit(page.page_size))).all()
        return Page(
            items=[convert(row) for row in rows], total=total,
            page_number=page.page_number, page_size=page.page_size,
        )


class PostgresUnitOfWork(PostgresRepository, UnitOfWork):
    async def commit(self) -> None:
        async with self._errors():
            await self.session.commit()

    async def rollback(self) -> None:
        try:
            await self.session.rollback()
        except Exception as error:  # noqa: BLE001 - откат после сбоя сам может не пройти
            # Соединение уже оборвано: откатывать нечего и некому. Наружу
            # уйдёт исходная причина сбоя, а не эта.
            logger.debug('Откат транзакции не удался: %s', error)


def _screening(row: ScreeningRow) -> Screening:
    return Screening.model_validate(row)


def _summary(score_sum: int | None, votes: int | None) -> RatingSummary:
    if not votes:
        return RatingSummary()
    return RatingSummary(average=round((score_sum or 0) / votes, 2), votes=votes)


class PostgresScreeningRepository(PostgresRepository, ScreeningRepository):
    async def add(self, draft: ScreeningDraft) -> Screening:
        row = ScreeningRow(**draft.model_dump())
        async with self._errors():
            self.session.add(row)
            await self.session.flush()
            await self.session.refresh(row)
        return _screening(row)

    async def get(self, screening_id: UUID, *, lock: bool = False) -> Screening | None:
        query = select(ScreeningRow).where(ScreeningRow.id == screening_id).execution_options(**FRESH)
        if lock:
            query = query.with_for_update()
        async with self._errors():
            row = await self.session.scalar(query)
        return _screening(row) if row else None

    async def update(self, screening_id: UUID, changes: ScreeningChanges) -> Screening | None:
        values = changes.model_dump(exclude_none=True)
        query = update(ScreeningRow).where(ScreeningRow.id == screening_id).values(**values)
        if changes.capacity is not None:
            # Мест не меньше занятых — условием той же команды, а не чтением
            # перед ней: между чтением и записью пролезла бы чужая бронь.
            query = query.where(ScreeningRow.seats_taken <= changes.capacity)
        async with self._errors():
            row = await self.session.scalar(query.returning(ScreeningRow).execution_options(**FRESH))
        return _screening(row) if row else None

    async def take_seats(self, screening_id: UUID, seats: int, now: datetime) -> Screening | None:
        # Сердце гарантии ФТ-12. Условие и изменение — одна команда: PostgreSQL
        # берёт блокировку строки, а второй такой же UPDATE после ожидания
        # перепроверяет условие на свежей версии строки (EvalPlanQual) и уже
        # видит увеличенный seats_taken. Последний рубеж — CHECK в таблице.
        query = (
            update(ScreeningRow)
            .where(
                ScreeningRow.id == screening_id,
                ScreeningRow.status == SCHEDULED,
                ScreeningRow.starts_at > now,
                ScreeningRow.seats_taken + seats <= ScreeningRow.capacity,
            )
            .values(seats_taken=ScreeningRow.seats_taken + seats)
            .returning(ScreeningRow)
            .execution_options(**FRESH)
        )
        try:
            async with self._errors():
                row = await self.session.scalar(query)
        except IntegrityError:
            # Сработал CHECK: условие выше разошлось с ограничением таблицы
            # (например, после правки кода). Для клиента это то же «мест
            # нет», а не 500; занятие мест — первая запись транзакции, и
            # откатывать, кроме неё, нечего.
            await self.session.rollback()
            logger.error('CHECK мест сработал в обход условия UPDATE: показ %s', screening_id)
            return None
        return _screening(row) if row else None

    async def release_seats(self, screening_id: UUID, seats: int) -> None:
        query = (
            update(ScreeningRow)
            .where(ScreeningRow.id == screening_id)
            .values(seats_taken=ScreeningRow.seats_taken - seats)
        )
        async with self._errors():
            await self.session.execute(query)

    async def cancel(self, screening_id: UUID) -> None:
        query = (
            update(ScreeningRow)
            .where(ScreeningRow.id == screening_id)
            .values(status=ScreeningStatus.CANCELLED.value)
        )
        async with self._errors():
            await self.session.execute(query)

    async def upcoming(
        self, now: datetime, page: PageRequest, film_id: UUID | None = None, host_id: UUID | None = None,
    ) -> Page[Screening]:
        query = select(ScreeningRow).where(ScreeningRow.status == SCHEDULED, ScreeningRow.starts_at > now)
        if film_id is not None:
            query = query.where(ScreeningRow.film_id == film_id)
        if host_id is not None:
            query = query.where(ScreeningRow.host_id == host_id)
        query = query.order_by(ScreeningRow.starts_at, ScreeningRow.id)
        return await self._page(query, page, lambda row: _screening(row[0]))

    async def of_host(self, host_id: UUID, period: Period, now: datetime, page: PageRequest) -> Page[Screening]:
        query = select(ScreeningRow).where(ScreeningRow.host_id == host_id)
        if period is Period.UPCOMING:
            query = query.where(ScreeningRow.starts_at > now).order_by(ScreeningRow.starts_at)
        else:
            query = query.where(ScreeningRow.starts_at <= now).order_by(ScreeningRow.starts_at.desc())
        return await self._page(query.order_by(ScreeningRow.id), page, lambda row: _screening(row[0]))

    async def hosts_of_film(self, film_id: UUID, now: datetime, page: PageRequest) -> Page[HostOffer]:
        rating = (
            select(UserRatingRow.user_id, UserRatingRow.score_sum, UserRatingRow.votes)
            .where(UserRatingRow.role == Role.HOST.value)
            .subquery()
        )
        next_start = func.min(ScreeningRow.starts_at).label('next_starts_at')
        query = (
            select(
                ScreeningRow.host_id,
                # Имя — из самого свежего показа хоста: если он сменил имя,
                # в блоке хостов будет новое.
                func.array_agg(aggregate_order_by(ScreeningRow.host_name, ScreeningRow.created_at.desc()))[1]
                .label('host_name'),
                func.count().label('screenings'),
                next_start,
                func.sum(ScreeningRow.capacity - ScreeningRow.seats_taken).label('seats_left'),
                rating.c.score_sum,
                rating.c.votes,
            )
            .outerjoin(rating, rating.c.user_id == ScreeningRow.host_id)
            .where(
                ScreeningRow.film_id == film_id,
                ScreeningRow.status == SCHEDULED,
                ScreeningRow.starts_at > now,
            )
            .group_by(ScreeningRow.host_id, rating.c.score_sum, rating.c.votes)
            .order_by(next_start, ScreeningRow.host_id)
        )
        return await self._page(query, page, lambda row: HostOffer(
            host_id=row.host_id,
            host_name=row.host_name,
            screenings=row.screenings,
            next_starts_at=row.next_starts_at,
            seats_left=row.seats_left,
            rating=_summary(row.score_sum, row.votes),
        ))


class PostgresBookingRepository(PostgresRepository, BookingRepository):
    async def add(self, draft: BookingDraft) -> Booking:
        row = BookingRow(**draft.model_dump())
        try:
            async with self._errors():
                self.session.add(row)
                await self.session.flush()
                await self.session.refresh(row)
        except IntegrityError as error:
            # Единственное ограничение, которое здесь может сработать, —
            # уникальность активной брони гостя: показ уже проверен и заблокирован.
            raise AlreadyExistsError(str(error.orig)) from error
        return Booking.model_validate(row)

    async def get(self, booking_id: UUID, *, lock: bool = False) -> Booking | None:
        query = select(BookingRow).where(BookingRow.id == booking_id).execution_options(**FRESH)
        if lock:
            query = query.with_for_update()
        async with self._errors():
            row = await self.session.scalar(query)
        return Booking.model_validate(row) if row else None

    async def active_of(self, screening_id: UUID, guest_id: UUID) -> Booking | None:
        query = select(BookingRow).where(
            BookingRow.screening_id == screening_id, BookingRow.guest_id == guest_id, BookingRow.status == ACTIVE,
        )
        async with self._errors():
            row = await self.session.scalar(query)
        return Booking.model_validate(row) if row else None

    async def change_seats(self, booking_id: UUID, seats: int) -> Booking:
        return await self._set(booking_id, seats=seats)

    async def cancel(self, booking_id: UUID) -> Booking:
        return await self._set(booking_id, status=BookingStatus.CANCELLED.value)

    async def cancel_all(self, screening_id: UUID) -> list[Booking]:
        query = (
            update(BookingRow)
            .where(BookingRow.screening_id == screening_id, BookingRow.status == ACTIVE)
            .values(status=BookingStatus.CANCELLED.value)
            .returning(BookingRow)
            .execution_options(**FRESH)
        )
        async with self._errors():
            rows = (await self.session.scalars(query)).all()
        return [Booking.model_validate(row) for row in rows]

    async def guests(self, screening_id: UUID) -> list[GuestEntry]:
        query = (
            select(BookingRow, UserRatingRow.score_sum, UserRatingRow.votes)
            .outerjoin(
                UserRatingRow,
                (UserRatingRow.user_id == BookingRow.guest_id) & (UserRatingRow.role == Role.GUEST.value),
            )
            .where(BookingRow.screening_id == screening_id, BookingRow.status == ACTIVE)
            .order_by(BookingRow.created_at)
        )
        async with self._errors():
            rows = (await self.session.execute(query)).all()
        return [
            GuestEntry(booking=Booking.model_validate(row[0]), rating=_summary(row.score_sum, row.votes))
            for row in rows
        ]

    async def of_guest(self, guest_id: UUID, period: Period, now: datetime, page: PageRequest) -> Page[BookingView]:
        query = (
            select(BookingRow, ScreeningRow)
            .join(ScreeningRow, ScreeningRow.id == BookingRow.screening_id)
            .where(BookingRow.guest_id == guest_id)
        )
        if period is Period.UPCOMING:
            query = query.where(ScreeningRow.starts_at > now).order_by(ScreeningRow.starts_at)
        else:
            query = query.where(ScreeningRow.starts_at <= now).order_by(ScreeningRow.starts_at.desc())
        return await self._page(query.order_by(BookingRow.id), page, lambda row: BookingView(
            booking=Booking.model_validate(row[0]), screening=_screening(row[1]),
        ))

    async def _set(self, booking_id: UUID, **values: Any) -> Booking:
        query = (
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(**values)
            .returning(BookingRow)
            .execution_options(**FRESH)
        )
        async with self._errors():
            row = await self.session.scalar(query)
        return Booking.model_validate(row)


class PostgresRatingRepository(PostgresRepository, RatingRepository):
    async def add(self, draft: RatingDraft) -> Rating:
        row = RatingRow(**draft.model_dump(exclude={'target_role'}), target_role=draft.target_role.value)
        # Агрегат двигается той же транзакцией: счётчик и оценки не разойдутся.
        bump = pg_insert(UserRatingRow).values(
            user_id=draft.target_id, role=draft.target_role.value, score_sum=draft.score, votes=1,
        )
        bump = bump.on_conflict_do_update(
            index_elements=[UserRatingRow.user_id, UserRatingRow.role],
            set_={
                'score_sum': UserRatingRow.score_sum + bump.excluded.score_sum,
                'votes': UserRatingRow.votes + 1,
            },
        )
        try:
            async with self._errors():
                self.session.add(row)
                await self.session.flush()
                await self.session.execute(bump)
                await self.session.refresh(row)
        except IntegrityError as error:
            raise AlreadyExistsError(str(error.orig)) from error
        return Rating.model_validate(row)

    async def by_author(self, screening_id: UUID, author_id: UUID) -> list[Rating]:
        query = (
            select(RatingRow)
            .where(RatingRow.screening_id == screening_id, RatingRow.author_id == author_id)
            .order_by(RatingRow.created_at)
        )
        async with self._errors():
            rows = (await self.session.scalars(query)).all()
        return [Rating.model_validate(row) for row in rows]

    async def received(self, user_id: UUID, role: Role, page: PageRequest) -> Page[Rating]:
        query = (
            select(RatingRow)
            .where(RatingRow.target_id == user_id, RatingRow.target_role == role.value)
            .order_by(RatingRow.created_at.desc(), RatingRow.id)
        )
        return await self._page(query, page, lambda row: Rating.model_validate(row[0]))

    async def summary(self, user_id: UUID) -> UserRating:
        ratings = select(UserRatingRow).where(UserRatingRow.user_id == user_id)
        # Имя — последний снимок: из показа, если зритель бывал хостом, иначе из брони.
        host_name = (
            select(ScreeningRow.host_name).where(ScreeningRow.host_id == user_id)
            .order_by(ScreeningRow.created_at.desc()).limit(1)
        )
        guest_name = (
            select(BookingRow.guest_name).where(BookingRow.guest_id == user_id)
            .order_by(BookingRow.created_at.desc()).limit(1)
        )
        async with self._errors():
            rows = {row.role: row for row in (await self.session.scalars(ratings)).all()}
            name = await self.session.scalar(host_name) or await self.session.scalar(guest_name)
        host, guest = rows.get(Role.HOST.value), rows.get(Role.GUEST.value)
        return UserRating(
            user_id=user_id,
            name=name,
            as_host=_summary(host.score_sum, host.votes) if host else RatingSummary(),
            as_guest=_summary(guest.score_sum, guest.votes) if guest else RatingSummary(),
        )


class PostgresOutbox(PostgresRepository, Outbox):
    """Outbox событий.

    `add` пишет в транзакцию бронирования и ничего не фиксирует. Методы
    ретранслятора (`claim`, `done`, `retry`) — его собственные короткие
    транзакции, они фиксируются сразу.
    """

    async def add(self, drafts: Sequence[OutboxDraft]) -> None:
        async with self._errors():
            self.session.add_all([
                OutboxRow(payload=draft.payload, request_id=draft.request_id or '-') for draft in drafts
            ])
            await self.session.flush()

    async def claim(self, limit: int, lease: timedelta, now: datetime) -> list[OutboxMessage]:
        # SKIP LOCKED: два ретранслятора разбирают разные события, а не ждут
        # друг друга на одних и тех же строках.
        due = (
            select(OutboxRow.id)
            .where(OutboxRow.available_at <= now, OutboxRow.rejected_at.is_(None))
            .order_by(OutboxRow.available_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        query = (
            update(OutboxRow)
            .where(OutboxRow.id.in_(due.scalar_subquery()))
            .values(available_at=now + lease, attempts=OutboxRow.attempts + 1)
            .returning(OutboxRow)
        )
        async with self._errors():
            rows = (await self.session.scalars(query)).all()
            messages = [OutboxMessage.model_validate(row) for row in rows]
            await self.session.commit()
        return messages

    async def done(self, message_id: UUID) -> None:
        async with self._errors():
            await self.session.execute(delete(OutboxRow).where(OutboxRow.id == message_id))
            await self.session.commit()

    async def retry(self, message_id: UUID, at: datetime, error: str) -> None:
        async with self._errors():
            await self.session.execute(
                update(OutboxRow).where(OutboxRow.id == message_id).values(available_at=at, last_error=error[:1000]),
            )
            await self.session.commit()

    async def reject(self, message_id: UUID, at: datetime, error: str) -> None:
        async with self._errors():
            await self.session.execute(
                update(OutboxRow).where(OutboxRow.id == message_id).values(rejected_at=at, last_error=error[:1000]),
            )
            await self.session.commit()

    async def rejected(self, limit: int) -> list[RejectedEvent]:
        query = (
            select(OutboxRow)
            .where(OutboxRow.rejected_at.is_not(None))
            .order_by(OutboxRow.rejected_at, OutboxRow.id)
            .limit(limit)
        )
        async with self._errors():
            rows = (await self.session.scalars(query)).all()
        return [RejectedEvent.model_validate(row) for row in rows]

    async def requeue(self, message_ids: Sequence[UUID] | None, now: datetime) -> int:
        query = update(OutboxRow).where(OutboxRow.rejected_at.is_not(None))
        if message_ids is not None:
            query = query.where(OutboxRow.id.in_(message_ids))
        query = query.values(rejected_at=None, available_at=now).returning(OutboxRow.id)
        async with self._errors():
            requeued = len((await self.session.scalars(query)).all())
            await self.session.commit()
        return requeued
