"""Ошибки в HTTP: статусы, тело ответа и их описание в OpenAPI.

Тело любой ошибки сервиса — `{"code": "...", "detail": "..."}`: `code` для
программ, `detail` для людей. Формат тот же, что у сервиса авторизации и
сервиса сбора событий: клиенту кинотеатра не нужно знать три разных формата
ошибок.

Исключение одно — 422 с ошибками проверки параметров в стандартном формате
FastAPI, но без присланных значений: рецензия может быть длинной, и повторять
её в ответе (а значит, и в журналах прокси) незачем.
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
    ForbiddenError,
    NotAuthenticatedError,
    NotFoundError,
    ServiceError,
    TokenExpiredError,
    TokenInvalidError,
)
from storage.base import StorageUnavailableError

logger = logging.getLogger(__name__)

# Схема аутентификации в заголовке WWW-Authenticate (RFC 6750).
SCHEME = 'Bearer'

STATUSES: dict[type[ServiceError], HTTPStatus] = {
    NotAuthenticatedError: HTTPStatus.UNAUTHORIZED,
    TokenExpiredError: HTTPStatus.UNAUTHORIZED,
    TokenInvalidError: HTTPStatus.UNAUTHORIZED,
    ForbiddenError: HTTPStatus.FORBIDDEN,
    NotFoundError: HTTPStatus.NOT_FOUND,
}
SERVICE_UNAVAILABLE_CODE = 'service_unavailable'
SERVICE_UNAVAILABLE_DETAIL = 'Service temporarily unavailable, retry later'

# Ошибки проверки токена — у всех эндпоинтов, где он нужен.
TOKEN_ERRORS = (NotAuthenticatedError, TokenExpiredError, TokenInvalidError)


class ErrorSchema(BaseModel):
    """Ошибка."""

    code: str = Field(description='Код ошибки для программ', examples=['review_not_found'])
    detail: str = Field(description='Описание для человека', examples=['Review not found'])


def status_of(error: type[ServiceError]) -> HTTPStatus:
    for category, status in STATUSES.items():
        if issubclass(error, category):
            return status
    return HTTPStatus.CONFLICT if error.__name__.endswith('AlreadyExistsError') else HTTPStatus.BAD_REQUEST


def error_body(code: str, detail: str) -> dict[str, str]:
    return {'code': code, 'detail': detail}


def error_responses(*errors: type[ServiceError]) -> dict[int | str, dict[str, Any]]:
    """Описание ответов с ошибками для OpenAPI: по статусу — все возможные коды с примерами.

    Собирается из самих классов ошибок, а не пишется руками у каждого
    эндпоинта: иначе документация разойдётся с поведением при первой же новой
    ошибке.
    """
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
    responses.update(SERVICE_UNAVAILABLE_RESPONSE)
    return responses


SERVICE_UNAVAILABLE_RESPONSE: dict[int | str, dict[str, Any]] = {
    HTTPStatus.SERVICE_UNAVAILABLE: {
        'model': ErrorSchema,
        'description': 'MongoDB временно недоступна',
        'content': {
            'application/json': {'example': error_body(SERVICE_UNAVAILABLE_CODE, SERVICE_UNAVAILABLE_DETAIL)},
        },
    },
}


async def service_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Ответ на ошибку бизнес-логики."""
    error = exc if isinstance(exc, ServiceError) else ServiceError()
    headers = None
    if isinstance(error, TOKEN_ERRORS):
        # RFC 6750: на 401 сервер сообщает схему аутентификации, а для
        # недействительного токена — ещё и причину.
        header = SCHEME
        if isinstance(error, (TokenExpiredError, TokenInvalidError)):
            header = f'{SCHEME} error="invalid_token", error_description="{error.message}"'
        headers = {'WWW-Authenticate': header}
    return JSONResponse(
        status_code=status_of(type(error)),
        content=error_body(error.code, error.message),
        headers=headers,
    )


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Ответ 422 в формате FastAPI, но без поля input.

    FastAPI возвращает в input присланное значение — то есть целиком текст
    рецензии. В ответе и в журналах прокси ему не место.
    """
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    cleaned = [{key: value for key, value in error.items() if key != 'input'} for error in errors]
    return JSONResponse(status_code=HTTPStatus.UNPROCESSABLE_ENTITY, content={'detail': jsonable_encoder(cleaned)})


async def storage_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    """Ответ 503, когда не ответило хранилище."""
    # Причина — в журнал, клиенту — без внутренних подробностей.
    logger.error('%s %s: %s', request.method, request.url.path, exc)
    return JSONResponse(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        content=error_body(SERVICE_UNAVAILABLE_CODE, SERVICE_UNAVAILABLE_DETAIL),
    )


HANDLERS: dict[type[Exception], Any] = {
    ServiceError: service_error_handler,
    RequestValidationError: validation_error_handler,
    StorageUnavailableError: storage_unavailable_handler,
}
