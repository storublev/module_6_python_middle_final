import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

import backoff
from elasticsearch import AsyncElasticsearch, ConnectionTimeout, NotFoundError
from elasticsearch import ConnectionError as ElasticConnectionError

from storage.base import Document, DocumentStorage, RelatedTo, SearchRequest, Sort, StorageUnavailableError, TextQuery

logger = logging.getLogger(__name__)

T = TypeVar('T')

INDEX_NOT_FOUND = 'index_not_found_exception'

# Текстовые поля сортируются по keyword-подполю `raw` из схемы индексов ETL.
KEYWORD_SUBFIELDS = {'title', 'name', 'full_name'}

# Сбои, при которых Elasticsearch не ответил.
CONNECTION_ERRORS = (ElasticConnectionError, ConnectionTimeout)
# Повторять стоит только отказ в соединении: Elasticsearch перезапускается или
# сеть моргнула. Таймаут не повторяется — каждая попытка заняла бы ещё
# request_timeout, а зависший Elasticsearch вряд ли ответит на вторую.
RETRYABLE_ERRORS = (ElasticConnectionError,)


@dataclass(frozen=True)
class BackoffPolicy:
    """Повторы с экспоненциальной паузой: factor * 2^n секунд, но не больше
    max_value; на все попытки одного запроса — не больше max_time секунд.
    Между паузами добавляется случайный разброс, чтобы воркеры не повторяли
    запросы одновременно.
    """

    max_time: float
    factor: float
    max_value: float


class ElasticStorage(DocumentStorage):
    """Хранилище документов в Elasticsearch.

    Переводит SearchRequest в запрос Elasticsearch. При отказе в соединении
    повторяет запрос с экспоненциальной паузой, а если Elasticsearch так и не
    ответил, сообщает об этом StorageUnavailableError.
    """

    def __init__(self, elastic: AsyncElasticsearch, retry: BackoffPolicy):
        self.elastic = elastic
        self._call_with_retry = backoff.on_exception(
            backoff.expo,
            RETRYABLE_ERRORS,
            max_time=retry.max_time,
            factor=retry.factor,
            max_value=retry.max_value,
            logger=logger,
            backoff_log_level=logging.WARNING,
        )(self._call)

    @staticmethod
    async def _call(method: Callable[..., Awaitable[T]], **params: Any) -> T:
        """Вызывает метод клиента и дожидается ответа.

        Методы AsyncElasticsearch обёрнуты синхронным декоратором и только
        возвращают корутину, поэтому backoff не распознаёт их как асинхронные:
        повторял бы получение корутины, а ошибка возникала бы при await, вне
        повторов. Обёртка — настоящая корутинная функция.
        """
        return await method(**params)

    async def get(self, index: str, doc_id: str, fields: Sequence[str]) -> Document | None:
        try:
            async with self._errors(index):
                doc = await self._call_with_retry(
                    self.elastic.get, index=index, id=doc_id, source_includes=list(fields),
                )
        except NotFoundError:
            return None
        return doc['_source']

    async def search(self, index: str, request: SearchRequest) -> list[Document]:
        params: dict[str, Any] = {
            'query': self._query(request),
            'from_': request.offset,
            'size': request.size,
            'source_includes': list(request.fields),
            # Общее число совпадений не нужно: считать его дорого.
            'track_total_hits': False,
        }
        if request.sort:
            params['sort'] = [self._sort(sort) for sort in request.sort]

        async with self._errors(index):
            response = await self._call_with_retry(self.elastic.search, index=index, **params)
        return [hit['_source'] for hit in response['hits']['hits']]

    def _query(self, request: SearchRequest) -> dict[str, Any]:
        must = [self._text(request.text)] if request.text else []
        filters = [self._related(request.related_to)] if request.related_to else []
        if not must and not filters:
            return {'match_all': {}}
        return {'bool': {'must': must, 'filter': filters}}

    @staticmethod
    def _text(text: TextQuery) -> dict[str, Any]:
        return {'multi_match': {'query': text.text, 'fields': list(text.fields), 'fuzziness': 'AUTO'}}

    @staticmethod
    def _related(related: RelatedTo) -> dict[str, Any]:
        by_field = [
            {'nested': {'path': field, 'query': {'term': {f'{field}.id': related.entity_id}}}}
            for field in related.fields
        ]
        return {'bool': {'should': by_field, 'minimum_should_match': 1}}

    @staticmethod
    def _sort(sort: Sort) -> dict[str, Any]:
        field = f'{sort.field}.raw' if sort.field in KEYWORD_SUBFIELDS else sort.field
        return {field: {'order': sort.order.value}}

    @asynccontextmanager
    async def _errors(self, index: str) -> AsyncIterator[None]:
        """Переводит сбои Elasticsearch в StorageUnavailableError.

        NotFoundError об отсутствии документа пропускается дальше как есть:
        это обычный ответ, а не сбой. Отсутствие индекса, наоборот, значит,
        что ETL ещё не загрузил данные или индекс удалён, — сервис не готов.
        """
        try:
            yield
        except NotFoundError as exc:
            if exc.error != INDEX_NOT_FOUND:
                raise
            logger.error('Индекс %s не найден в Elasticsearch', index)
            raise StorageUnavailableError from exc
        except CONNECTION_ERRORS as exc:
            logger.error('Elasticsearch недоступен, индекс %s: %s', index, exc)
            raise StorageUnavailableError from exc
