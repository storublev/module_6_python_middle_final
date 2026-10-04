"""Общие параметры запросов для списков: пагинация, сортировка, поиск."""

from enum import StrEnum
from http import HTTPStatus
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from services.base import Pagination

# Elasticsearch по умолчанию не отдаёт документы дальше 10 000-го (index.max_result_window).
MAX_RESULT_WINDOW = 10_000
MAX_PAGE_SIZE = 100


class FilmSort(StrEnum):
    """Сортировка фильмов: минус в начале — по убыванию."""

    imdb_rating_desc = '-imdb_rating'
    imdb_rating_asc = 'imdb_rating'


def get_pagination(
    page_number: Annotated[int, Query(ge=1, description='Номер страницы, начиная с 1')] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description='Количество элементов на странице')] = 50,
) -> Pagination:
    if page_number * page_size > MAX_RESULT_WINDOW:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=f'page_number * page_size must not exceed {MAX_RESULT_WINDOW}',
        )
    return Pagination(page_number=page_number, page_size=page_size)


FilmSortQuery = Annotated[FilmSort, Query(description='Поле сортировки, минус — по убыванию')]

SearchQuery = Annotated[str, Query(min_length=1, description='Строка поиска', examples=['star'])]

PaginationDep = Annotated[Pagination, Depends(get_pagination)]
