"""Оценки фильмов: поставить, снять, посмотреть агрегат и свой список."""

from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Path

from api.dependencies import Likes, Page
from api.errors import error_responses
from api.security import CurrentUser
from models.content import FilmRating, Like, LikeRequest
from models.content import Page as PageModel
from services.errors import (
    NotAuthenticatedError,
    RatingNotFoundError,
    TokenExpiredError,
    TokenInvalidError,
)

router = APIRouter(tags=['Оценки'])

FilmId = Path(description='Идентификатор фильма в каталоге')
TOKEN_ERRORS = (NotAuthenticatedError, TokenExpiredError, TokenInvalidError)


@router.put(
    '/films/{film_id}/rating',
    response_model=Like,
    summary='Поставить или изменить оценку фильма',
    description=(
        'Оценка от 0 до 10: 10 — лайк, 0 — дизлайк. У зрителя на фильм одна оценка, '
        'повторный запрос её заменяет. Зритель берётся из access-токена.'
    ),
    responses=error_responses(*TOKEN_ERRORS),
)
async def rate_film(user: CurrentUser, service: Likes, body: LikeRequest, film_id: UUID = FilmId) -> Like:
    return await service.rate(film_id, user.user_id, body.rating)


@router.delete(
    '/films/{film_id}/rating',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Снять свою оценку фильма',
    responses=error_responses(*TOKEN_ERRORS, RatingNotFoundError),
)
async def unrate_film(user: CurrentUser, service: Likes, film_id: UUID = FilmId) -> None:
    await service.unrate(film_id, user.user_id)


@router.get(
    '/films/{film_id}/rating/me',
    response_model=Like,
    summary='Посмотреть свою оценку фильма',
    description='Своя оценка видна сразу после записи — это требование НФТ-6.',
    responses=error_responses(*TOKEN_ERRORS, RatingNotFoundError),
)
async def my_rating(user: CurrentUser, service: Likes, film_id: UUID = FilmId) -> Like:
    return await service.my_rating(film_id, user.user_id)


@router.get(
    '/films/{film_id}/rating',
    response_model=FilmRating,
    summary='Лайки, дизлайки и средняя оценка фильма',
    description=(
        'Показывается в карточке фильма, поэтому доступно и без токена. '
        'Фильм без единой оценки — не ошибка: в ответе нули и `average_rating: null`.'
    ),
    responses=error_responses(),
)
async def film_rating(service: Likes, film_id: UUID = FilmId) -> FilmRating:
    return await service.film_rating(film_id)


@router.get(
    '/users/me/likes',
    response_model=PageModel[Like],
    summary='Понравившиеся мне фильмы',
    description='Фильмы, которым зритель поставил 6 и выше, от новых к старым.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def liked_films(user: CurrentUser, service: Likes, page: Page) -> PageModel[Like]:
    return await service.liked_films(user.user_id, page.page, page.size)
