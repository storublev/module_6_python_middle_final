"""Показы: создать, найти, изменить, отменить; гости, брони и оценки показа."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from api.dependencies import BookingServiceDep, RatingServiceDep, ScreeningServiceDep
from api.errors import TOKEN_ERRORS, error_responses
from api.security import CurrentUser, optional_authorization
from api.v1.schemas import (
    BookingCreateSchema,
    BookingSchema,
    GuestSchema,
    PageSchema,
    PaginationDep,
    RatingCreateSchema,
    RatingSchema,
    ScreeningCreateSchema,
    ScreeningSchema,
    ScreeningUpdateSchema,
)
from models.domain import Booking, GuestEntry, Page, Rating, Screening, ScreeningChanges
from services.errors import (
    AlreadyBookedError,
    AlreadyRatedError,
    CapacityBelowBookedError,
    CapacityOutOfRangeError,
    FilmNotBookableError,
    FilmNotFoundError,
    InvalidRatingTargetError,
    NotEnoughSeatsError,
    NothingToChangeError,
    NotParticipantError,
    NotScreeningHostError,
    OwnScreeningError,
    RatingTooEarlyError,
    ScreeningCancelledError,
    ScreeningClosedError,
    ScreeningNotFoundError,
    SeatsOutOfRangeError,
    StartsTooLateError,
    StartsTooSoonError,
)
from services.screenings import NewScreening

router = APIRouter()


@router.post(
    '',
    response_model=ScreeningSchema,
    status_code=HTTPStatus.CREATED,
    summary='Создать показ',
    description=(
        'Зритель становится хостом: предлагает фильм, место, время и число мест. Фильм проверяется в каталоге — '
        'показ можно устроить только на полнометражный (`type = movie`). Время — не раньше чем через 30 минут '
        'и не дальше года вперёд.'
    ),
    responses=error_responses(
        *TOKEN_ERRORS, StartsTooSoonError, StartsTooLateError, CapacityOutOfRangeError, FilmNotBookableError,
        FilmNotFoundError,
    ),
)
async def create_screening(
    body: ScreeningCreateSchema,
    user: CurrentUser,
    service: ScreeningServiceDep,
    authorization: Annotated[str | None, Depends(optional_authorization)],
) -> Screening:
    data = NewScreening(**body.model_dump())
    return await service.create(user.user_id, data, authorization)


@router.get(
    '',
    response_model=PageSchema[ScreeningSchema],
    summary='Будущие показы',
    description='Запланированные и ещё не начавшиеся показы по времени начала. Фильтры по фильму и хосту '
                'дают ровно то, что видит зритель, выбрав в карточке фильма хоста: его даты и время.',
)
async def list_screenings(
    service: ScreeningServiceDep,
    pagination: PaginationDep,
    film_id: Annotated[UUID | None, Query(description='Только показы этого фильма')] = None,
    host_id: Annotated[UUID | None, Query(description='Только показы этого хоста')] = None,
) -> Page[Screening]:
    return await service.upcoming(pagination, film_id=film_id, host_id=host_id)


@router.get(
    '/{screening_id}',
    response_model=ScreeningSchema,
    summary='Показ',
    responses=error_responses(ScreeningNotFoundError),
)
async def get_screening(screening_id: UUID, service: ScreeningServiceDep) -> Screening:
    return await service.get(screening_id)


@router.patch(
    '/{screening_id}',
    response_model=ScreeningSchema,
    summary='Изменить показ',
    description='Только хост и только до начала. Мест нельзя сделать меньше, чем забронировано. '
                'О смене времени или места гости получают письмо.',
    responses=error_responses(
        *TOKEN_ERRORS, NotScreeningHostError, ScreeningNotFoundError, ScreeningClosedError,
        CapacityBelowBookedError, NothingToChangeError, StartsTooSoonError, StartsTooLateError,
        CapacityOutOfRangeError,
    ),
)
async def update_screening(
    screening_id: UUID, body: ScreeningUpdateSchema, user: CurrentUser, service: ScreeningServiceDep,
) -> Screening:
    changes = ScreeningChanges(**body.model_dump(exclude_unset=True))
    return await service.update(user.user_id, screening_id, changes)


@router.post(
    '/{screening_id}/cancel',
    response_model=ScreeningSchema,
    summary='Отменить показ',
    description='Только хост и только до начала. Все брони отменяются, гости получают письмо.',
    responses=error_responses(*TOKEN_ERRORS, NotScreeningHostError, ScreeningNotFoundError, ScreeningClosedError),
)
async def cancel_screening(screening_id: UUID, user: CurrentUser, service: ScreeningServiceDep) -> Screening:
    return await service.cancel(user.user_id, screening_id)


@router.get(
    '/{screening_id}/bookings',
    response_model=list[GuestSchema],
    summary='Гости показа',
    description='Активные брони с рейтингом каждого гостя. Только для хоста: это персональные данные.',
    responses=error_responses(*TOKEN_ERRORS, NotScreeningHostError, ScreeningNotFoundError),
)
async def screening_guests(screening_id: UUID, user: CurrentUser, service: BookingServiceDep) -> list[GuestEntry]:
    return await service.guests(user.user_id, screening_id)


@router.post(
    '/{screening_id}/bookings',
    response_model=BookingSchema,
    status_code=HTTPStatus.CREATED,
    summary='Забронировать места',
    description=(
        'Занимает места на показе. **Больше мест, чем осталось у хоста, забронировать нельзя** — ни одним '
        'запросом, ни одновременными запросами разных гостей: проверка и запись мест — одна команда базы. '
        'У гостя одна активная бронь на показ; чтобы взять больше мест, её меняют.'
    ),
    responses=error_responses(
        *TOKEN_ERRORS, OwnScreeningError, ScreeningNotFoundError, NotEnoughSeatsError, ScreeningClosedError,
        AlreadyBookedError, SeatsOutOfRangeError,
    ),
)
async def book(
    screening_id: UUID, body: BookingCreateSchema, user: CurrentUser, service: BookingServiceDep,
) -> Booking:
    return await service.book(user.user_id, screening_id, body.seats)


@router.post(
    '/{screening_id}/ratings',
    response_model=RatingSchema,
    status_code=HTTPStatus.CREATED,
    summary='Оценить участника показа',
    description='После начала показа гость с бронью оценивает хоста, хост — своих гостей. '
                'Одна оценка на пару за показ.',
    responses=error_responses(
        *TOKEN_ERRORS, NotParticipantError, ScreeningNotFoundError, RatingTooEarlyError, ScreeningCancelledError,
        AlreadyRatedError, InvalidRatingTargetError,
    ),
)
async def rate(
    screening_id: UUID, body: RatingCreateSchema, user: CurrentUser, service: RatingServiceDep,
) -> Rating:
    return await service.rate(user.user_id, screening_id, body.target_id, body.score, body.comment)


@router.get(
    '/{screening_id}/ratings/mine',
    response_model=list[RatingSchema],
    summary='Мои оценки на показе',
    description='Кого автор уже оценил на этом показе.',
    responses=error_responses(*TOKEN_ERRORS, ScreeningNotFoundError),
)
async def my_ratings(screening_id: UUID, user: CurrentUser, service: RatingServiceDep) -> list[Rating]:
    return await service.mine(user.user_id, screening_id)
