"""Идентификатор запроса в админке.

Запрос сотрудника проходит nginx, админку и сервис авторизации. nginx выдаёт
ему идентификатор и передаёт заголовком `X-Request-Id`; админка пишет его в
свой журнал и передаёт дальше, в вызовы сервиса авторизации. По одному
идентификатору собирается вся история входа — и в журналах, и в Jaeger.

Хранится он в контекстной переменной: она своя у каждого потока, поэтому
одновременные запросы не перепутают идентификаторы, а клиенту сервиса
авторизации не приходится тащить его параметром через весь бэкенд входа.
"""

import logging
from collections.abc import Callable
from contextvars import ContextVar

from django.http import HttpRequest, HttpResponse

HEADER = 'X-Request-Id'
META_KEY = 'HTTP_X_REQUEST_ID'
# Записи вне обработки запроса (команды, старт) идентификатора не имеют.
NO_REQUEST = '-'

_request_id: ContextVar[str] = ContextVar('request_id', default=NO_REQUEST)


def get_request_id() -> str:
    return _request_id.get()


class RequestIdMiddleware:
    """Запоминает идентификатор запроса и возвращает его в ответе.

    Заголовок ставит nginx. Запрос мимо него идентификатора не получит — в
    отличие от сервисов API, админка такой запрос не отклоняет: в неё ходят
    люди браузером, и отказ со ссылкой на заголовок им ничего не объяснит.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.META.get(META_KEY, '').strip()
        token = _request_id.set(request_id or NO_REQUEST)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        if request_id:
            response[HEADER] = request_id
        return response


class RequestIdFilter(logging.Filter):
    """Подставляет идентификатор запроса в каждую запись журнала."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True
