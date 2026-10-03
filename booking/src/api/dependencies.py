"""Сборка сервисов для эндпоинтов (Composition Root).

Только здесь выбираются конкретные реализации: PostgreSQL для показов, броней
и оценок, Async API и сервис авторизации по HTTP. Бизнес-логика получает их
через конструктор и зависит от интерфейсов из `storage/base.py`, поэтому
ничего не знает ни о SQLAlchemy, ни о httpx, ни о FastAPI.

Все репозитории запроса делят одну сессию базы: так единица работы фиксирует
бронь, счётчик мест и событие для писем одной транзакцией.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from db.postgres import get_session
from services.bookings import BookingService
from services.letters import Letters
from services.people import NameResolver
from services.ratings import RatingService
from services.screenings import ScreeningRules, ScreeningService
from storage.postgres import (
    PostgresBookingRepository,
    PostgresOutbox,
    PostgresRatingRepository,
    PostgresScreeningRepository,
    PostgresUnitOfWork,
)

DbSession = Annotated[AsyncSession, Depends(get_session)]

RULES = ScreeningRules(
    min_capacity=settings.min_capacity,
    max_capacity=settings.max_capacity,
    min_lead_time=settings.min_lead_time,
    max_lead_time=settings.max_lead_time,
)
LETTERS = Letters(settings.public_base_url, settings.display_timezone)


def now() -> datetime:
    return datetime.now(UTC)


def get_screening_service(request: Request, session: DbSession) -> ScreeningService:
    return ScreeningService(
        uow=PostgresUnitOfWork(session),
        screenings=PostgresScreeningRepository(session),
        bookings=PostgresBookingRepository(session),
        outbox=PostgresOutbox(session),
        catalog=request.app.state.catalog,
        names=NameResolver(request.app.state.people),
        letters=LETTERS,
        rules=RULES,
        clock=now,
    )


def get_booking_service(request: Request, session: DbSession) -> BookingService:
    return BookingService(
        uow=PostgresUnitOfWork(session),
        screenings=PostgresScreeningRepository(session),
        bookings=PostgresBookingRepository(session),
        outbox=PostgresOutbox(session),
        names=NameResolver(request.app.state.people),
        letters=LETTERS,
        max_seats=settings.max_seats_per_booking,
        clock=now,
    )


def get_rating_service(session: DbSession) -> RatingService:
    return RatingService(
        uow=PostgresUnitOfWork(session),
        screenings=PostgresScreeningRepository(session),
        bookings=PostgresBookingRepository(session),
        ratings=PostgresRatingRepository(session),
        clock=now,
    )


ScreeningServiceDep = Annotated[ScreeningService, Depends(get_screening_service)]
BookingServiceDep = Annotated[BookingService, Depends(get_booking_service)]
RatingServiceDep = Annotated[RatingService, Depends(get_rating_service)]
