import hashlib
from dataclasses import dataclass
from functools import cache
from typing import Any, ClassVar, Generic, TypeVar
from uuid import UUID

import orjson
from elasticsearch import AsyncElasticsearch, NotFoundError
from pydantic import BaseModel, TypeAdapter

from core.config import settings
from services.cache import Cache

ModelT = TypeVar('ModelT', bound=BaseModel)
ItemT = TypeVar('ItemT', bound=BaseModel)


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
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return self.model.model_validate_json(cached)

        try:
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
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return adapter.validate_json(cached)

        try:
            response = await self.elastic.search(index=self.index, track_total_hits=False, **params)
        except NotFoundError:
            # Индекс ещё не создан ETL — это пустой результат, а не ошибка сервера.
            return []

        items = [model.model_validate(hit['_source']) for hit in response['hits']['hits']]
        await self.cache.set(cache_key, adapter.dump_json(items), settings.cache_expire_in_seconds)
        return items

    def _search_cache_key(self, params: dict[str, Any]) -> str:
        digest = hashlib.md5(orjson.dumps(params, option=orjson.OPT_SORT_KEYS)).hexdigest()
        return f'{self.index}:search:{digest}'
