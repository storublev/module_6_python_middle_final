"""Рецензии: опубликовать, удалить свою, проголосовать, прочитать список."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from api.dependencies import Page, Reviews
from api.errors import error_responses
from api.security import CurrentUser
from models.content import Page as PageModel
from models.content import Review, ReviewRequest, ReviewSort, ReviewVoteRequest
from services.errors import (
    NotAuthenticatedError,
    NotReviewAuthorError,
    OwnReviewVoteError,
    ReviewAlreadyExistsError,
    ReviewNotFoundError,
    TokenExpiredError,
    TokenInvalidError,
)

router = APIRouter(tags=['Рецензии'])

FilmId = Path(description='Идентификатор фильма в каталоге')
ReviewId = Path(description='Идентификатор рецензии')
TOKEN_ERRORS = (NotAuthenticatedError, TokenExpiredError, TokenInvalidError)


@router.post(
    '/films/{film_id}/reviews',
    response_model=Review,
    status_code=HTTPStatus.CREATED,
    summary='Написать рецензию на фильм',
    description='Одна рецензия зрителя на фильм. Оценка фильма в теле запроса необязательна.',
    responses=error_responses(*TOKEN_ERRORS, ReviewAlreadyExistsError),
)
async def publish_review(
    user: CurrentUser,
    service: Reviews,
    body: ReviewRequest,
    film_id: UUID = FilmId,
) -> Review:
    return await service.publish(film_id, user.user_id, body.text, body.rating)


@router.get(
    '/films/{film_id}/reviews',
    response_model=PageModel[Review],
    summary='Рецензии на фильм',
    description=(
        'Порядок выбирается параметром `sort`. Сортировок несколько намеренно: показывать '
        'только самые полезные — ловушка, новая рецензия никогда не набрала бы голосов. '
        'Доступно без токена.'
    ),
    responses=error_responses(),
)
async def list_reviews(
    service: Reviews,
    page: Page,
    film_id: UUID = FilmId,
    sort: Annotated[ReviewSort, Query(description='Порядок рецензий')] = ReviewSort.NEWEST,
) -> PageModel[Review]:
    return await service.film_reviews(film_id, sort, page.page, page.size)


@router.delete(
    '/reviews/{review_id}',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Удалить свою рецензию',
    description='Удалить можно только собственную рецензию; чужая отвечает 403, а не 404.',
    responses=error_responses(*TOKEN_ERRORS, ReviewNotFoundError, NotReviewAuthorError),
)
async def delete_review(user: CurrentUser, service: Reviews, review_id: UUID = ReviewId) -> None:
    await service.withdraw(review_id, user.user_id)


@router.put(
    '/reviews/{review_id}/vote',
    response_model=Review,
    summary='Оценить полезность чужой рецензии',
    description=(
        'Повторный голос заменяет прежний, а не добавляет второй. За свою рецензию '
        'голосовать нельзя: иначе автор накручивал бы себе полезность.'
    ),
    responses=error_responses(*TOKEN_ERRORS, ReviewNotFoundError, OwnReviewVoteError),
)
async def vote_review(
    user: CurrentUser,
    service: Reviews,
    body: ReviewVoteRequest,
    review_id: UUID = ReviewId,
) -> Review:
    return await service.vote(review_id, user.user_id, body.useful)
