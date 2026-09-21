"""Приём идентификатора запроса на входе сервиса."""

from http import HTTPStatus

from flask import Flask, request
from opentelemetry import trace

from api.errors import error_response
from core.request_id import HEADER, NO_REQUEST, set_request_id

# Тег спана, по которому запрос ищется в Jaeger: тот же идентификатор виден и
# в журнале nginx, и в записях сервисов.
SPAN_ATTRIBUTE = 'http.request_id'


def register_request_id(app: Flask, required: bool, exempt_paths: frozenset[str] = frozenset()) -> None:
    """Кладёт `X-Request-Id` в контекст запроса, в спан трассировки и в ответ.

    Заголовок ставит nginx, поэтому его отсутствие значит, что запрос пришёл
    мимо шлюза, — и по умолчанию такой запрос отклоняется: без идентификатора
    его не найти ни в журналах, ни в Jaeger. Проверку выключает настройка
    `UGC_REQUIRE_REQUEST_ID`: сервис, запущенный локально без nginx, иначе не
    отладить. Документация и проверка живости освобождены всегда — их дёргает
    healthcheck контейнера, минуя шлюз.
    """

    @app.before_request
    def receive_request_id():
        request_id = (request.headers.get(HEADER) or '').strip()
        if not request_id and required and request.path not in exempt_paths:
            return error_response(
                HTTPStatus.BAD_REQUEST,
                'request_id_required',
                f'{HEADER} header is required; it is set by the gateway',
            )
        set_request_id(request_id or NO_REQUEST)
        if request_id:
            trace.get_current_span().set_attribute(SPAN_ATTRIBUTE, request_id)
        return None

    @app.after_request
    def return_request_id(response):
        # Клиент может назвать эту строку в обращении в поддержку, и запрос
        # найдётся во всех журналах сразу.
        request_id = (request.headers.get(HEADER) or '').strip()
        if request_id:
            response.headers[HEADER] = request_id
        return response
