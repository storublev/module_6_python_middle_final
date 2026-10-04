"""Публичное чтение: хосты фильма и рейтинги зрителей. Вход не нужен."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from api.dependencies import RatingServiceDep, ScreeningServiceDep
from api.v1.schemas import HostOfferSchema, PageSchema, PaginationDep, RatingSchema, UserRatingSchema
from models.domain import HostOffer, Page, Rating, Role, UserRating

router = APIRouter()


@router.get(
    '/films/{film_id}/hosts',
    response_model=PageSchema[HostOfferSchema],
    summary='Хосты фильма',
    description='Кто предлагает этот фильм: имя, рейтинг хоста, ближайший показ и свободные места. '
                'Первый шаг выбора в карточке фильма: хост → дата и время → бронь.',
)
async def film_hosts(film_id: UUID, service: ScreeningServiceDep, pagination: PaginationDep) -> Page[HostOffer]:
    return await service.hosts_of_film(film_id, pagination)


@router.get(
    '/users/{user_id}/rating',
    response_model=UserRatingSchema,
    summary='Рейтинг зрителя',
    description='Средняя оценка как хоста и как гостя. У того, кого ещё не оценивали, — пустой рейтинг, а не 404.',
)
async def user_rating(user_id: UUID, service: RatingServiceDep) -> UserRating:
    return await service.summary(user_id)


@router.get(
    '/users/{user_id}/reviews',
    response_model=PageSchema[RatingSchema],
    summary='Отзывы о зрителе',
    description='Оценки с комментариями, полученные зрителем в одной роли, новые сверху.',
)
async def user_reviews(
    user_id: UUID,
    service: RatingServiceDep,
    pagination: PaginationDep,
    role: Annotated[Role, Query(description='host — отзывы гостей о нём как о хосте, guest — наоборот')] = Role.HOST,
) -> Page[Rating]:
    return await service.received(user_id, role, pagination)
