"""Закладки: отложить фильм, убрать, посмотреть список."""

from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Path

from api.dependencies import Bookmarks, Page
from api.errors import error_responses
from api.security import CurrentUser
from models.content import Bookmark
from models.content import Page as PageModel
from services.errors import (
    BookmarkNotFoundError,
    NotAuthenticatedError,
    TokenExpiredError,
    TokenInvalidError,
)

router = APIRouter(tags=['Закладки'])

FilmId = Path(description='Идентификатор фильма в каталоге')
TOKEN_ERRORS = (NotAuthenticatedError, TokenExpiredError, TokenInvalidError)


@router.put(
    '/films/{film_id}/bookmark',
    response_model=Bookmark,
    summary='Отложить фильм на потом',
    description=(
        'Операция идемпотентна: повторный запрос не создаёт вторую закладку и не '
        'меняет время добавления. Поэтому PUT, а не POST.'
    ),
    responses=error_responses(*TOKEN_ERRORS),
)
async def add_bookmark(user: CurrentUser, service: Bookmarks, film_id: UUID = FilmId) -> Bookmark:
    return await service.add(film_id, user.user_id)


@router.delete(
    '/films/{film_id}/bookmark',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Убрать фильм из закладок',
    responses=error_responses(*TOKEN_ERRORS, BookmarkNotFoundError),
)
async def remove_bookmark(user: CurrentUser, service: Bookmarks, film_id: UUID = FilmId) -> None:
    await service.remove(film_id, user.user_id)


@router.get(
    '/users/me/bookmarks',
    response_model=PageModel[Bookmark],
    summary='Мои закладки',
    description='В порядке добавления: закладок у зрителя немного, сортировать их незачем.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def list_bookmarks(user: CurrentUser, service: Bookmarks, page: Page) -> PageModel[Bookmark]:
    return await service.list_for_user(user.user_id, page.page, page.size)
