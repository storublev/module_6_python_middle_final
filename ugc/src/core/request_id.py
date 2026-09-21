"""Идентификатор запроса, общий для всей цепочки сервисов.

Тот же приём, что в Async API и сервисе авторизации: nginx выдаёт каждому
входящему запросу `X-Request-Id`, сервис пишет его в свои записи журнала, в
тег спана и в ответ. По одной строке собирается история запроса по всем
сервисам сразу.

Хранится в контекстной переменной. Под gevent она своя у каждого зелёного
потока — ровно как у задачи asyncio в остальных сервисах, — поэтому
одновременные запросы не перепутают идентификаторы.
"""

import logging
from contextvars import ContextVar

HEADER = 'X-Request-Id'
# Записи вне обработки запроса (старт сервиса, остановка) идентификатора не
# имеют; в журнале это видно как «-».
NO_REQUEST = '-'

_request_id: ContextVar[str] = ContextVar('request_id', default=NO_REQUEST)


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str:
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    """Подставляет идентификатор запроса в каждую запись журнала."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True
