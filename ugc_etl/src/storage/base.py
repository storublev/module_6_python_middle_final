"""Интерфейсы источника и приёмника событий.

Бизнес-логика переноса знает, что события откуда-то читаются и куда-то
вставляются, и не знает ни про Kafka, ни про ClickHouse: в тестах на их место
встают источник и приёмник в памяти.

Контракт для всех реализаций: сбой выходит наружу как `SourceUnavailableError`
или `SinkUnavailableError`, а не как исключение библиотеки. Решать, что делать
дальше, должен вызывающий код — от конкретной библиотеки это не зависит.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any


class SourceUnavailableError(Exception):
    """Источник событий не отвечает: брокер недоступен, таймаут, обрыв соединения."""


class SinkUnavailableError(Exception):
    """Аналитическое хранилище не приняло строки: недоступно, таймаут или отказ вставки."""


class EventSource(ABC):
    """Источник событий — поток пачек."""

    @abstractmethod
    def batches(self) -> Iterable[Sequence[dict]]:
        """Отдаёт пачки событий, пока источник не закроют.

        Пачка может оказаться пустой — значит, за отведённое время новых
        событий не пришло; это не ошибка, а обычное ночное затишье.

        Raises:
            SourceUnavailableError: источник перестал отвечать.
        """

    @abstractmethod
    def commit(self) -> None:
        """Подтверждает обработку последней пачки.

        Вызывается только после успешной вставки: падение между вставкой и
        подтверждением приведёт к повтору пачки, а не к её потере.

        Raises:
            SourceUnavailableError: подтвердить не удалось.
        """

    @abstractmethod
    def close(self) -> None:
        """Закрывает соединения при остановке."""


class EventSink(ABC):
    """Приёмник событий — аналитическое хранилище."""

    @abstractmethod
    def insert(self, rows: Sequence[Sequence[Any]]) -> None:
        """Вставляет пачку строк.

        Raises:
            SinkUnavailableError: вставить не удалось.
        """

    @abstractmethod
    def close(self) -> None:
        """Закрывает соединения при остановке."""
