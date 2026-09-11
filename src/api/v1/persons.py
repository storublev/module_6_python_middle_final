from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from api.v1.params import FilmSort, SearchQuery, get_pagination
from api.v1.schemas import FilmShortSchema, PersonSchema
from models.film import FilmShort
from models.person import Person
from services.base import Pagination
from services.film import FilmService, get_film_service
from services.person import PersonService, get_person_service

router = APIRouter()

PERSON_NOT_FOUND = 'person not found'


@router.get(
    '/search',
    response_model=list[PersonSchema],
    summary='Поиск по персонам',
    description='Поиск по имени, сортировка по релевантности.',
)
async def person_search(
    query: SearchQuery,
    pagination: Annotated[Pagination, Depends(get_pagination)],
    person_service: Annotated[PersonService, Depends(get_person_service)],
) -> list[Person]:
    return await person_service.search(query, pagination)


@router.get(
    '/{person_id}',
    response_model=PersonSchema,
    summary='Данные по персоне',
    description='Имя персоны и фильмы, в которых она участвовала, с её ролями.',
    responses={HTTPStatus.NOT_FOUND: {'description': PERSON_NOT_FOUND}},
)
async def person_details(
    person_id: UUID,
    person_service: Annotated[PersonService, Depends(get_person_service)],
) -> Person:
    person = await person_service.get_by_id(person_id)
    if not person:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=PERSON_NOT_FOUND)
    return person


@router.get(
    '/{person_id}/film',
    response_model=list[FilmShortSchema],
    summary='Фильмы по персоне',
    responses={HTTPStatus.NOT_FOUND: {'description': PERSON_NOT_FOUND}},
)
async def person_films(
    person_id: UUID,
    pagination: Annotated[Pagination, Depends(get_pagination)],
    person_service: Annotated[PersonService, Depends(get_person_service)],
    film_service: Annotated[FilmService, Depends(get_film_service)],
    sort: Annotated[FilmSort, Query(description='Поле сортировки, минус — по убыванию')] = FilmSort.imdb_rating_desc,
) -> list[FilmShort]:
    if not await person_service.get_by_id(person_id):
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=PERSON_NOT_FOUND)
    return await film_service.get_by_person(person_id, pagination, sort=sort)
