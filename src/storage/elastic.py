import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from elasticsearch import AsyncElasticsearch, ConnectionTimeout, NotFoundError
from elasticsearch import ConnectionError as ElasticConnectionError

from storage.base import Document, DocumentStorage, RelatedTo, SearchRequest, Sort, StorageUnavailableError, TextQuery

logger = logging.getLogger(__name__)

INDEX_NOT_FOUND = 'index_not_found_exception'

# Текстовые поля сортируются по keyword-подполю `raw` из схемы индексов ETL.
KEYWORD_SUBFIELDS = {'title', 'name', 'full_name'}


class ElasticStorage(DocumentStorage):
    """Хранилище документов в Elasticsearch.

    Переводит SearchRequest в запрос Elasticsearch, а сбои Elasticsearch —
    в StorageUnavailableError.
    """

    def __init__(self, elastic: AsyncElasticsearch):
        self.elastic = elastic

    async def get(self, index: str, doc_id: str, fields: Sequence[str]) -> Document | None:
        try:
            async with self._errors(index):
                doc = await self.elastic.get(index=index, id=doc_id, source_includes=list(fields))
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
            response = await self.elastic.search(index=index, **params)
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
        except (ElasticConnectionError, ConnectionTimeout) as exc:
            logger.error('Elasticsearch недоступен, индекс %s: %s', index, exc)
            raise StorageUnavailableError from exc
