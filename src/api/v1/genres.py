from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.v1.params import get_pagination
from api.v1.schemas import GenreSchema
from models.genre import Genre
from services.base import Pagination
from services.genre import GenreService, get_genre_service

router = APIRouter()

GENRE_NOT_FOUND = 'genre not found'


@router.get(
    '',
    response_model=list[GenreSchema],
    summary='Список жанров',
    description='Жанры в алфавитном порядке.',
)
async def genre_list(
    pagination: Annotated[Pagination, Depends(get_pagination)],
    genre_service: Annotated[GenreService, Depends(get_genre_service)],
) -> list[Genre]:
    return await genre_service.get_list(pagination)


@router.get(
    '/{genre_id}',
    response_model=GenreSchema,
    summary='Данные по жанру',
    responses={HTTPStatus.NOT_FOUND: {'description': GENRE_NOT_FOUND}},
)
async def genre_details(
    genre_id: UUID,
    genre_service: Annotated[GenreService, Depends(get_genre_service)],
) -> Genre:
    genre = await genre_service.get_by_id(genre_id)
    if not genre:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=GENRE_NOT_FOUND)
    return genre
