from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from api.v1.params import FilmSort, SearchQuery, get_pagination
from api.v1.schemas import FilmSchema, FilmShortSchema
from models.film import Film, FilmShort
from services.base import Pagination
from services.film import FilmService, get_film_service

router = APIRouter()

FILM_NOT_FOUND = 'film not found'


@router.get(
    '',
    response_model=list[FilmShortSchema],
    summary='Список фильмов',
    description='Популярные фильмы с сортировкой по рейтингу и фильтром по жанру. '
                'С фильтром по жанру фильма возвращает похожие фильмы.',
)
async def film_list(
    pagination: Annotated[Pagination, Depends(get_pagination)],
    film_service: Annotated[FilmService, Depends(get_film_service)],
    sort: Annotated[FilmSort, Query(description='Поле сортировки, минус — по убыванию')] = FilmSort.imdb_rating_desc,
    genre: Annotated[UUID | None, Query(description='uuid жанра для фильтрации')] = None,
) -> list[FilmShort]:
    return await film_service.get_list(pagination, sort=sort, genre_id=genre)


@router.get(
    '/search',
    response_model=list[FilmShortSchema],
    summary='Поиск по фильмам',
    description='Полнотекстовый поиск по названию и описанию, сортировка по релевантности.',
)
async def film_search(
    query: SearchQuery,
    pagination: Annotated[Pagination, Depends(get_pagination)],
    film_service: Annotated[FilmService, Depends(get_film_service)],
) -> list[FilmShort]:
    return await film_service.search(query, pagination)


@router.get(
    '/{film_id}',
    response_model=FilmSchema,
    summary='Полная информация по фильму',
    responses={HTTPStatus.NOT_FOUND: {'description': FILM_NOT_FOUND}},
)
async def film_details(
    film_id: UUID,
    film_service: Annotated[FilmService, Depends(get_film_service)],
) -> Film:
    film = await film_service.get_by_id(film_id)
    if not film:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=FILM_NOT_FOUND)
    return film
