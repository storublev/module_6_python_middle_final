"""Ошибки API в том же формате, что у сервиса авторизации.

Тело ошибки — `{"code": "...", "detail": "..."}`: `code` машиночитаемый, по
нему клиент решает, что делать (повторить запрос, обновить токен, выбросить
событие), `detail` — человеку в журнал. Сообщения на английском, как и в
остальных сервисах кинотеатра.
"""

from http import HTTPStatus

from flask import Flask, jsonify
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException


class ApiError(Exception):
    """Ошибка, которую обработчик возвращает клиенту как есть."""

    def __init__(self, status: HTTPStatus, code: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


def error_response(status: HTTPStatus, code: str, detail: str):
    return jsonify({'code': code, 'detail': detail}), status


def register_error_handlers(app: Flask) -> None:
    """Приводит все ошибки приложения к общему формату.

    Без этого Flask отдал бы на неизвестный адрес HTML-страницу, а на
    неразобранный JSON — стандартный текст werkzeug: клиенту, который ждёт
    `code`, это одинаково бесполезно.
    """

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return error_response(error.status, error.code, error.detail)

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        # Значения, присланные клиентом, в ответ не попадают: вернуть их
        # значило бы разложить их по журналам прокси и браузера.
        first = error.errors()[0]
        location = '.'.join(str(part) for part in first['loc']) or 'body'
        return error_response(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            'invalid_request',
            f'{location}: {first["msg"]}',
        )

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        status = HTTPStatus(error.code or HTTPStatus.INTERNAL_SERVER_ERROR)
        return error_response(status, status.name.lower(), error.description or status.phrase)

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        app.logger.exception('Необработанная ошибка: %s', error)
        # Наружу — ни текста исключения, ни трассировки: они в журнале.
        return error_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            'internal_error',
            'Internal server error',
        )
