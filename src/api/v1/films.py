from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.dependencies import AccessDep, FilmServiceDep
from api.v1.params import FilmSort, FilmSortQuery, PaginationDep, SearchQuery
from api.v1.schemas import FilmSchema, FilmShortSchema, error_response
from models.film import Film, FilmShort, FilmType

router = APIRouter()

FILM_NOT_FOUND = 'film not found'
SUBSCRIPTION_REQUIRED = 'film is available by subscription only'
TOKEN_REJECTED = 'auth service rejected the token'  # noqa: S105 — текст ошибки, а не секрет
SUBSCRIPTION_UNVERIFIABLE = 'subscription cannot be verified now, retry later'

# Списки и поиск отдают только то, что открыто пользователю, а не помечают
# закрытое: иначе страницы выходили бы разной длины.
VISIBILITY_NOTE = (
    'Видны только доступные пользователю фильмы: без токена — публичные, с токеном подписчика — '
    'ещё и вышедшие менее трёх лет назад. Пока сервис авторизации недоступен, выдача сужается '
    'до публичных фильмов.'
)
# Эндпоинты, читающие токен, могут получить его отказ от сервиса авторизации.
# Тип указан явно: FastAPI ждёт ключи int | str, а HTTPStatus — подкласс int,
# который в аннотации словаря сам по себе не подходит.
TOKEN_RESPONSES: dict[int | str, dict[str, Any]] = {
    HTTPStatus.UNAUTHORIZED: error_response(TOKEN_REJECTED),
}


@router.get(
    '',
    response_model=list[FilmShortSchema],
    summary='Список фильмов',
    description='Популярные фильмы с сортировкой по рейтингу и фильтром по жанру. '
                'С фильтром по жанру фильма возвращает похожие фильмы. ' + VISIBILITY_NOTE,
    responses=TOKEN_RESPONSES,
)
async def film_list(
    pagination: PaginationDep,
    access: AccessDep,
    film_service: FilmServiceDep,
    sort: FilmSortQuery = FilmSort.imdb_rating_desc,
    genre: Annotated[UUID | None, Query(description='uuid жанра для фильтрации')] = None,
    film_type: Annotated[
        FilmType | None,
        Query(alias='type', description='Тип: movie — полнометражные (их можно бронировать), tv_show — сериалы'),
    ] = None,
) -> list[FilmShort]:
    return await film_service.get_list(pagination, access, sort=sort, genre_id=genre, film_type=film_type)


@router.get(
    '/search',
    response_model=list[FilmShortSchema],
    summary='Поиск по фильмам',
    description='Полнотекстовый поиск по названию и описанию, сортировка по релевантности. ' + VISIBILITY_NOTE,
    responses=TOKEN_RESPONSES,
)
async def film_search(
    query: SearchQuery,
    pagination: PaginationDep,
    access: AccessDep,
    film_service: FilmServiceDep,
) -> list[FilmShort]:
    return await film_service.search(query, pagination, access)


@router.get(
    '/{film_id}',
    response_model=FilmSchema,
    summary='Полная информация по фильму',
    description='Фильм, вышедший менее трёх лет назад, доступен только по подписке: нужен токен '
                'пользователя с правом `films.subscription`. Пока сервис авторизации недоступен, '
                'на такой фильм отвечаем 503: подписку не подтвердить, а отказывать в ней неверно.',
    responses={
        **TOKEN_RESPONSES,
        HTTPStatus.FORBIDDEN: error_response(SUBSCRIPTION_REQUIRED),
        HTTPStatus.NOT_FOUND: error_response(FILM_NOT_FOUND),
        HTTPStatus.SERVICE_UNAVAILABLE: error_response(SUBSCRIPTION_UNVERIFIABLE),
    },
)
async def film_details(
    film_id: UUID,
    access: AccessDep,
    film_service: FilmServiceDep,
) -> Film:
    film = await film_service.get_by_id(film_id, access)
    if not film:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=FILM_NOT_FOUND)
    return film
