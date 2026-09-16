"""Интерфейс хранилища документов.

Сервисы описывают, что им нужно найти, структурой SearchRequest, а как это
выполнить — решает реализация хранилища. Поэтому сервисы не зависят от
Elasticsearch: другое хранилище подключается новой реализацией
DocumentStorage, без правок в бизнес-логике.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

Document = dict[str, Any]


class StorageUnavailableError(Exception):
    """Хранилище не может ответить: нет соединения, истёк таймаут или нет индекса.

    Это временная неготовность сервиса, а не ошибка в запросе клиента.
    """


class SortOrder(StrEnum):
    ASC = 'asc'
    DESC = 'desc'


@dataclass(frozen=True)
class Sort:
    field: str
    order: SortOrder = SortOrder.ASC


@dataclass(frozen=True)
class SearchField:
    """Поле полнотекстового поиска и его вес.

    Совпадение в поле с весом 3 влияет на релевантность втрое сильнее,
    чем в поле с весом 1. Как выразить вес на языке запросов, решает
    реализация хранилища.
    """

    name: str
    weight: float = 1

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError(f'Вес поля {self.name} должен быть положительным: {self.weight}')


@dataclass(frozen=True)
class TextQuery:
    """Полнотекстовый поиск с опечатками по нескольким полям."""

    text: str
    fields: tuple[SearchField, ...]


@dataclass(frozen=True)
class RelatedTo:
    """Документ связан с сущностью `entity_id` хотя бы через одно из полей `fields`.

    Поля — списки вложенных объектов с `id`: жанры фильма, актёры, сценаристы.
    """

    entity_id: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class FieldIn:
    """Значение поля документа входит в перечисленные.

    Отбор, а не поиск: на релевантность не влияет. Так сервисы ограничивают
    выдачу — например, уровнями доступа, открытыми пользователю.
    """

    field: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class SearchRequest:
    """Страница документов: какие поля вернуть, что искать, чем ограничить и как сортировать."""

    fields: tuple[str, ...]
    offset: int = 0
    size: int = 50
    text: TextQuery | None = None
    related_to: RelatedTo | None = None
    filters: tuple[FieldIn, ...] = ()
    sort: tuple[Sort, ...] = ()


class DocumentStorage(ABC):
    """Хранилище документов с поиском."""

    @abstractmethod
    async def get(self, index: str, doc_id: str, fields: Sequence[str]) -> Document | None:
        """Возвращает поля документа по id или None, если документа нет.

        Raises:
            StorageUnavailableError: хранилище не может ответить.
        """

    @abstractmethod
    async def search(self, index: str, request: SearchRequest) -> list[Document]:
        """Возвращает страницу документов, подходящих под запрос.

        Raises:
            StorageUnavailableError: хранилище не может ответить.
        """
