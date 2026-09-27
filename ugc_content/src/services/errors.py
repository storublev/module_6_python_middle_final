"""Ошибки бизнес-логики.

Слой services не знает про HTTP, поэтому ошибки здесь — про предметную
область: «нет такой рецензии», «это чужая рецензия», «уже оценено».
Во что они превращаются в ответе, решает api/errors.py. Недоступность
хранилища сюда не входит: это `StorageUnavailableError` из storage/base.py —
ошибка не предметной области, а инфраструктуры.

У каждой ошибки есть машиночитаемый `code` — тот же формат, что у сервиса
авторизации: `{"code": "...", "detail": "..."}`.
"""


class ServiceError(Exception):
    """Ошибка бизнес-логики с кодом для программ и описанием для людей."""

    code = 'service_error'
    message = 'Service error'

    def __init__(self, message: str | None = None) -> None:
        self.message = message or type(self).message
        super().__init__(self.message)


class NotAuthenticatedError(ServiceError):
    """Запрос без токена там, где он обязателен."""

    code = 'not_authenticated'
    message = 'Authorization header is required'


class TokenExpiredError(ServiceError):
    """Срок действия access-токена истёк."""

    code = 'token_expired'  # noqa: S105 — код ошибки, не секрет
    message = 'Access token has expired'


class TokenInvalidError(ServiceError):
    """Токен не принят: подпись, формат или тип."""

    code = 'token_invalid'  # noqa: S105 — код ошибки, не секрет
    message = 'Access token is invalid'


class NotFoundError(ServiceError):
    """Запрошенного объекта нет."""

    code = 'not_found'
    message = 'Object not found'


class ReviewNotFoundError(NotFoundError):
    """Рецензии с таким идентификатором нет."""

    code = 'review_not_found'
    message = 'Review not found'


class RatingNotFoundError(NotFoundError):
    """Зритель не оценивал этот фильм."""

    code = 'rating_not_found'
    message = 'The user has not rated this film'


class BookmarkNotFoundError(NotFoundError):
    """Фильма нет в закладках зрителя."""

    code = 'bookmark_not_found'
    message = 'The film is not bookmarked'


class ForbiddenError(ServiceError):
    """Действие над чужим объектом."""

    code = 'forbidden'
    message = 'Action is not allowed'


class NotReviewAuthorError(ForbiddenError):
    """Удалить рецензию может только её автор."""

    code = 'not_review_author'
    message = 'Only the author can delete the review'


class OwnReviewVoteError(ForbiddenError):
    """Голосовать за полезность собственной рецензии нельзя."""

    code = 'own_review_vote'
    message = 'Voting for your own review is not allowed'


class ReviewAlreadyExistsError(ServiceError):
    """У зрителя уже есть рецензия на этот фильм."""

    code = 'review_already_exists'
    message = 'The user has already reviewed this film'
