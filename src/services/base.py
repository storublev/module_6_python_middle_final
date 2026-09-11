import hashlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import cache
from typing import Any, ClassVar, Generic, TypeVar
from uuid import UUID

import orjson
from elasticsearch import AsyncElasticsearch, ConnectionTimeout, NotFoundError
from elasticsearch import ConnectionError as ElasticConnectionError
from pydantic import BaseModel, TypeAdapter, ValidationError

from core.config import settings
from services.cache import Cache
from services.exceptions import StorageUnavailableError

logger = logging.getLogger(__name__)

ModelT = TypeVar('ModelT', bound=BaseModel)
ItemT = TypeVar('ItemT', bound=BaseModel)
T = TypeVar('T')

INDEX_NOT_FOUND = 'index_not_found_exception'


@dataclass(frozen=True)
class Pagination:
    page_number: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page_number - 1) * self.page_size


@cache
def _list_adapter(model: type[ItemT]) -> TypeAdapter[list[ItemT]]:
    """TypeAdapter дорого создавать, поэтому он строится один раз на модель."""
    return TypeAdapter(list[model])


def sort_by(field: str) -> list[dict[str, Any]]:
    """Переводит `-imdb_rating` в сортировку Elasticsearch.

    Минус в начале — сортировка по убыванию. Вторым ключом идёт `id`, чтобы
    порядок документов с одинаковым значением был стабильным между страницами.
    """
    order = 'desc' if field.startswith('-') else 'asc'
    return [{field.lstrip('-'): {'order': order}}, {'id': {'order': 'asc'}}]


def nested_term(path: str, field: str, value: UUID | str) -> dict[str, Any]:
    """Точное совпадение по полю вложенного (nested) объекта."""
    return {'nested': {'path': path, 'query': {'term': {f'{path}.{field}': str(value)}}}}


class BaseService(Generic[ModelT]):
    """Чтение документов одного индекса Elasticsearch с кешированием в Redis.

    Наследник задаёт индекс и модель документа, а сам описывает только запросы.
    Ответы кешируются по набору параметров запроса: одинаковые запросы
    в течение `cache_expire_in_seconds` не доходят до Elasticsearch.
    """

    index: ClassVar[str]
    model: ClassVar[type[BaseModel]]

    def __init__(self, elastic: AsyncElasticsearch, cache_storage: Cache):
        self.elastic = elastic
        self.cache = cache_storage

    async def get_by_id(self, item_id: UUID) -> ModelT | None:
        """Возвращает документ по id или None, если его нет."""
        cache_key = f'{self.index}:id:{item_id}'
        cached = await self._cache_get(cache_key, self.model.model_validate_json)
        if cached is not None:
            return cached

        try:
            async with self._elastic_errors():
                doc = await self.elastic.get(
                    index=self.index,
                    id=str(item_id),
                    source_includes=list(self.model.model_fields),
                )
        except NotFoundError:
            return None

        item = self.model.model_validate(doc['_source'])
        await self.cache.set(cache_key, item.model_dump_json(), settings.cache_expire_in_seconds)
        return item

    async def _search(
        self,
        model: type[ItemT],
        pagination: Pagination,
        query: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
    ) -> list[ItemT]:
        """Ищет документы и отдаёт страницу результатов в виде моделей `model`.

        Из Elasticsearch запрашиваются только поля модели — для списков
        не тянем описание и составы участников.
        """
        params: dict[str, Any] = {
            'query': query or {'match_all': {}},
            'from_': pagination.offset,
            'size': pagination.page_size,
            'source_includes': list(model.model_fields),
        }
        if sort:
            params['sort'] = sort

        adapter = _list_adapter(model)
        cache_key = self._search_cache_key(params)
        cached = await self._cache_get(cache_key, adapter.validate_json)
        if cached is not None:
            return cached

        async with self._elastic_errors():
            response = await self.elastic.search(index=self.index, track_total_hits=False, **params)

        items = [model.model_validate(hit['_source']) for hit in response['hits']['hits']]
        await self.cache.set(cache_key, adapter.dump_json(items), settings.cache_expire_in_seconds)
        return items

    def _search_cache_key(self, params: dict[str, Any]) -> str:
        digest = hashlib.md5(orjson.dumps(params, option=orjson.OPT_SORT_KEYS)).hexdigest()
        return f'{self.index}:search:{digest}'

    async def _cache_get(self, key: str, validate: Callable[[bytes], T]) -> T | None:
        """Читает запись из кеша; повреждённую запись считает отсутствующей.

        Запись может оказаться некорректным JSON или перестать соответствовать
        модели после обновления приложения. Тогда данные читаются из
        Elasticsearch, а запись в кеше перезаписывается свежей.
        """
        cached = await self.cache.get(key)
        if cached is None:
            return None
        try:
            return validate(cached)
        except ValidationError as exc:
            logger.warning('Запись %s в кеше не прошла проверку, читаем из Elasticsearch: %s', key, exc)
            return None

    @asynccontextmanager
    async def _elastic_errors(self) -> AsyncIterator[None]:
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
            logger.error('Индекс %s не найден в Elasticsearch', self.index)
            raise StorageUnavailableError from exc
        except (ElasticConnectionError, ConnectionTimeout) as exc:
            logger.error('Elasticsearch недоступен, индекс %s: %s', self.index, exc)
            raise StorageUnavailableError from exc
