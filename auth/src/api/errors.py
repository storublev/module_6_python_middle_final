"""Ошибки в HTTP: статусы, тело ответа и их описание в OpenAPI.

Тело любой ошибки сервиса — `{"code": "...", "detail": "..."}`: `code` для
программ, `detail` для людей. Исключение — 422 с ошибками проверки
параметров в стандартном формате FastAPI.
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.errors import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    NotAuthenticatedError,
    NotFoundError,
    PermissionDeniedError,
    ServiceError,
    ServiceUnavailableError,
    TokenExpiredError,
    TokenInvalidError,
    TokenRevokedError,
    TooManyRequestsError,
)

logger = logging.getLogger(__name__)

STATUSES: dict[type[ServiceError], HTTPStatus] = {
    AuthenticationError: HTTPStatus.UNAUTHORIZED,
    ForbiddenError: HTTPStatus.FORBIDDEN,
    NotFoundError: HTTPStatus.NOT_FOUND,
    ConflictError: HTTPStatus.CONFLICT,
    TooManyRequestsError: HTTPStatus.TOO_MANY_REQUESTS,
    ServiceUnavailableError: HTTPStatus.SERVICE_UNAVAILABLE,
}
RETRY_AFTER_HEADER = {
    'Retry-After': {
        'description': 'Через сколько секунд можно повторить попытку',
        'schema': {'type': 'integer', 'example': 60},
    },
}
SERVICE_UNAVAILABLE_CODE = 'service_unavailable'
SERVICE_UNAVAILABLE_DETAIL = 'Service temporarily unavailable, retry later'

# Ошибки проверки access-токена — у всех эндпоинтов, где он нужен.
TOKEN_ERRORS = (NotAuthenticatedError, TokenExpiredError, TokenInvalidError, TokenRevokedError)
# Эндпоинты управления доступом требуют ещё и права access.manage.
ADMIN_ERRORS = (*TOKEN_ERRORS, PermissionDeniedError)


class ErrorSchema(BaseModel):
    """Ошибка."""

    code: str = Field(description='Код ошибки для программ', examples=['token_expired'])
    detail: str = Field(description='Описание для человека', examples=['Access token has expired'])


def status_of(error: type[ServiceError]) -> HTTPStatus:
    for category, status in STATUSES.items():
        if issubclass(error, category):
            return status
    return HTTPStatus.INTERNAL_SERVER_ERROR


def error_body(code: str, detail: str) -> dict[str, str]:
    return {'code': code, 'detail': detail}


def error_responses(*errors: type[ServiceError]) -> dict[int | str, dict[str, Any]]:
    """Описание ответов с ошибками для OpenAPI: по статусу — все возможные коды с примерами."""
    grouped: dict[HTTPStatus, list[type[ServiceError]]] = {}
    for error in errors:
        grouped.setdefault(status_of(error), []).append(error)
    responses: dict[int | str, dict[str, Any]] = {}
    for status, status_errors in grouped.items():
        responses[status] = {
            'model': ErrorSchema,
            'description': ', '.join(f'`{error.code}`' for error in status_errors),
            'content': {
                'application/json': {
                    'examples': {
                        error.code: {'summary': error.message, 'value': error_body(error.code, error.message)}
                        for error in status_errors
                    },
                },
            },
        }
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            responses[status]['headers'] = RETRY_AFTER_HEADER
    return responses


# Тип указан явно: FastAPI ждёт ключи int | str, а HTTPStatus — подкласс int,
# который в аннотации словаря сам по себе не подходит.
SERVICE_UNAVAILABLE_RESPONSE: dict[int | str, dict[str, Any]] = {
    HTTPStatus.SERVICE_UNAVAILABLE: {
        'model': ErrorSchema,
        'description': 'PostgreSQL или Redis временно недоступны',
        'content': {
            'application/json': {'example': error_body(SERVICE_UNAVAILABLE_CODE, SERVICE_UNAVAILABLE_DETAIL)},
        },
    },
}


async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    headers = None
    if isinstance(exc, AuthenticationError):
        # RFC 6750: на 401 сервер сообщает схему аутентификации, а для
        # недействительного токена — ещё и причину.
        header = 'Bearer'
        if isinstance(exc, (TokenExpiredError, TokenInvalidError, TokenRevokedError)):
            header = f'Bearer error="invalid_token", error_description="{exc.message}"'
        headers = {'WWW-Authenticate': header}
    elif isinstance(exc, TooManyRequestsError):
        headers = {'Retry-After': str(exc.retry_after)}
    return JSONResponse(status_code=status_of(type(exc)), content=error_body(exc.code, exc.message), headers=headers)


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Ответ 422 в формате FastAPI, но без поля input.

    FastAPI возвращает в input присланное значение, а это может быть пароль:
    сервис авторизации не повторяет клиенту и прокси введённые данные.
    """
    errors = [{key: value for key, value in error.items() if key != 'input'} for error in exc.errors()]
    return JSONResponse(status_code=HTTPStatus.UNPROCESSABLE_ENTITY, content={'detail': jsonable_encoder(errors)})


async def storage_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    # Причина — в журнал, клиенту — без внутренних подробностей.
    logger.error('%s %s: %s', request.method, request.url.path, exc)
    return JSONResponse(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        content=error_body(SERVICE_UNAVAILABLE_CODE, SERVICE_UNAVAILABLE_DETAIL),
    )
