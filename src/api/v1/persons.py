from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, HTTPException

from api.dependencies import AccessDep, FilmServiceDep, PersonServiceDep
from api.v1.films import TOKEN_RESPONSES, VISIBILITY_NOTE
from api.v1.params import FilmSort, FilmSortQuery, PaginationDep, SearchQuery
from api.v1.schemas import FilmShortSchema, PersonSchema, error_response
from models.film import FilmShort
from models.person import Person

router = APIRouter()

PERSON_NOT_FOUND = 'person not found'


@router.get(
    '',
    response_model=list[PersonSchema],
    summary='Список персон',
    description='Персоны в алфавитном порядке.',
)
async def person_list(
    pagination: PaginationDep,
    person_service: PersonServiceDep,
) -> list[Person]:
    return await person_service.get_list(pagination)


@router.get(
    '/search',
    response_model=list[PersonSchema],
    summary='Поиск по персонам',
    description='Поиск по имени, сортировка по релевантности.',
)
async def person_search(
    query: SearchQuery,
    pagination: PaginationDep,
    person_service: PersonServiceDep,
) -> list[Person]:
    return await person_service.search(query, pagination)


@router.get(
    '/{person_id}',
    response_model=PersonSchema,
    summary='Данные по персоне',
    description='Имя персоны и фильмы, в которых она участвовала, с её ролями.',
    responses={HTTPStatus.NOT_FOUND: error_response(PERSON_NOT_FOUND)},
)
async def person_details(
    person_id: UUID,
    person_service: PersonServiceDep,
) -> Person:
    person = await person_service.get_by_id(person_id)
    if not person:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=PERSON_NOT_FOUND)
    return person


@router.get(
    '/{person_id}/film',
    response_model=list[FilmShortSchema],
    summary='Фильмы по персоне',
    description=VISIBILITY_NOTE,
    responses={**TOKEN_RESPONSES, HTTPStatus.NOT_FOUND: error_response(PERSON_NOT_FOUND)},
)
async def person_films(
    person_id: UUID,
    pagination: PaginationDep,
    access: AccessDep,
    person_service: PersonServiceDep,
    film_service: FilmServiceDep,
    sort: FilmSortQuery = FilmSort.imdb_rating_desc,
) -> list[FilmShort]:
    if not await person_service.get_by_id(person_id):
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=PERSON_NOT_FOUND)
    return await film_service.get_by_person(person_id, pagination, access, sort=sort)
