import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import ClassVar, Generic, TypeVar
from uuid import UUID

import orjson
from pydantic import BaseModel, TypeAdapter, ValidationError

from core.config import settings
from services.cache import Cache
from storage.base import DocumentStorage, RelatedTo, SearchRequest, Sort, SortOrder, TextQuery

logger = logging.getLogger(__name__)

ModelT = TypeVar('ModelT', bound=BaseModel)
ItemT = TypeVar('ItemT', bound=BaseModel)
T = TypeVar('T')


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


def sort_by(field: str) -> tuple[Sort, ...]:
    """Переводит `-imdb_rating` в сортировку хранилища.

    Минус в начале — сортировка по убыванию. Вторым ключом идёт `id`, чтобы
    порядок документов с одинаковым значением был стабильным между страницами.
    """
    order = SortOrder.DESC if field.startswith('-') else SortOrder.ASC
    return Sort(field.lstrip('-'), order), Sort('id')


class BaseService(Generic[ModelT]):
    """Чтение документов одного индекса с кешированием.

    Наследник задаёт индекс и модель документа, а сам описывает только запросы.
    Ответы кешируются по набору параметров запроса: одинаковые запросы
    в течение `cache_expire_in_seconds` не доходят до хранилища.
    """

    index: ClassVar[str]
    model: ClassVar[type[BaseModel]]

    def __init__(self, storage: DocumentStorage, cache_storage: Cache):
        self.storage = storage
        self.cache = cache_storage

    async def get_by_id(self, item_id: UUID) -> ModelT | None:
        """Возвращает документ по id или None, если его нет."""
        cache_key = f'{self.index}:id:{item_id}'
        cached = await self._cache_get(cache_key, self.model.model_validate_json)
        if cached is not None:
            return cached

        doc = await self.storage.get(self.index, str(item_id), fields=tuple(self.model.model_fields))
        if doc is None:
            return None

        item = self.model.model_validate(doc)
        await self.cache.set(cache_key, item.model_dump_json(), settings.cache_expire_in_seconds)
        return item

    async def _search(
        self,
        model: type[ItemT],
        pagination: Pagination,
        text: TextQuery | None = None,
        related_to: RelatedTo | None = None,
        sort: tuple[Sort, ...] = (),
    ) -> list[ItemT]:
        """Ищет документы и отдаёт страницу результатов в виде моделей `model`.

        Из хранилища запрашиваются только поля модели — для списков
        не тянем описание и составы участников.
        """
        request = SearchRequest(
            fields=tuple(model.model_fields),
            offset=pagination.offset,
            size=pagination.page_size,
            text=text,
            related_to=related_to,
            sort=sort,
        )

        adapter = _list_adapter(model)
        cache_key = self._search_cache_key(request)
        cached = await self._cache_get(cache_key, adapter.validate_json)
        if cached is not None:
            return cached

        docs = await self.storage.search(self.index, request)
        items = [model.model_validate(doc) for doc in docs]
        await self.cache.set(cache_key, adapter.dump_json(items), settings.cache_expire_in_seconds)
        return items

    def _search_cache_key(self, request: SearchRequest) -> str:
        digest = hashlib.md5(orjson.dumps(request, option=orjson.OPT_SORT_KEYS)).hexdigest()
        return f'{self.index}:search:{digest}'

    async def _cache_get(self, key: str, validate: Callable[[bytes], T]) -> T | None:
        """Читает запись из кеша; повреждённую запись считает отсутствующей.

        Запись может оказаться некорректным JSON или перестать соответствовать
        модели после обновления приложения. Тогда данные читаются из
        хранилища, а запись в кеше перезаписывается свежей.
        """
        cached = await self.cache.get(key)
        if cached is None:
            return None
        try:
            return validate(cached)
        except ValidationError as exc:
            logger.warning('Запись %s в кеше не прошла проверку, читаем из хранилища: %s', key, exc)
            return None
