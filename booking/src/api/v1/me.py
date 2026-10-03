"""Кабинет: расписание хоста и брони гостя."""

from typing import Annotated

from fastapi import APIRouter, Query

from api.dependencies import BookingServiceDep, ScreeningServiceDep
from api.errors import TOKEN_ERRORS, error_responses
from api.security import CurrentUser
from api.v1.schemas import BookingViewSchema, PageSchema, PaginationDep, ScreeningSchema
from models.domain import BookingView, Page, Period, Screening

router = APIRouter()

PeriodQuery = Annotated[Period, Query(description='upcoming — ещё не начавшиеся, past — прошедшие')]


@router.get(
    '/screenings',
    response_model=PageSchema[ScreeningSchema],
    summary='Моё расписание',
    description='Показы, где зритель — хост, включая отменённые. Будущие — по времени, прошедшие — новые сверху.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def my_screenings(
    user: CurrentUser, service: ScreeningServiceDep, pagination: PaginationDep, period: PeriodQuery = Period.UPCOMING,
) -> Page[Screening]:
    return await service.of_host(user.user_id, period, pagination)


@router.get(
    '/bookings',
    response_model=PageSchema[BookingViewSchema],
    summary='Мои брони',
    description='Брони зрителя вместе с показами, включая отменённые.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def my_bookings(
    user: CurrentUser, service: BookingServiceDep, pagination: PaginationDep, period: PeriodQuery = Period.UPCOMING,
) -> Page[BookingView]:
    return await service.of_guest(user.user_id, period, pagination)
