"""Брони гостя: изменить число мест или отменить."""

from uuid import UUID

from fastapi import APIRouter

from api.dependencies import BookingServiceDep
from api.errors import WRITE_TOKEN_ERRORS, error_responses
from api.security import ActiveUser
from api.v1.schemas import BookingSchema, BookingUpdateSchema
from models.domain import Booking
from services.errors import (
    BookingCancelledError,
    BookingNotFoundError,
    NotBookingOwnerError,
    NotEnoughSeatsError,
    NothingToChangeError,
    ScreeningClosedError,
    SeatsOutOfRangeError,
)

router = APIRouter()


@router.patch(
    '/{booking_id}',
    response_model=BookingSchema,
    summary='Изменить число мест',
    description='Добирает недостающие места с той же гарантией, что и бронь, или возвращает лишние. '
                'Только до начала показа.',
    responses=error_responses(
        *WRITE_TOKEN_ERRORS, NotBookingOwnerError, BookingNotFoundError, NotEnoughSeatsError, ScreeningClosedError,
        BookingCancelledError, SeatsOutOfRangeError, NothingToChangeError,
    ),
)
async def change_booking(
    booking_id: UUID, body: BookingUpdateSchema, user: ActiveUser, service: BookingServiceDep,
) -> Booking:
    return await service.change_seats(user.user_id, booking_id, body.seats)


@router.post(
    '/{booking_id}/cancel',
    response_model=BookingSchema,
    summary='Отменить бронь',
    description='Места возвращаются хосту, хост получает письмо. Только до начала показа.',
    responses=error_responses(
        *WRITE_TOKEN_ERRORS, NotBookingOwnerError, BookingNotFoundError, ScreeningClosedError, BookingCancelledError,
    ),
)
async def cancel_booking(booking_id: UUID, user: ActiveUser, service: BookingServiceDep) -> Booking:
    return await service.cancel(user.user_id, booking_id)
