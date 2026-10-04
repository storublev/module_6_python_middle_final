import hashlib
from dataclasses import dataclass
from typing import ClassVar, cast
from uuid import UUID

import orjson
from pydantic import BaseModel

from services.cache import ModelCache
from storage.base import DocumentStorage, FieldIn, RelatedTo, SearchRequest, Sort, SortOrder, TextQuery


@dataclass(frozen=True)
class Pagination:
    page_number: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page_number - 1) * self.page_size


def sort_by(field: str) -> tuple[Sort, ...]:
    """Переводит `-imdb_rating` в сортировку хранилища.

    Минус в начале — сортировка по убыванию. Вторым ключом идёт `id`, чтобы
    порядок документов с одинаковым значением был стабильным между страницами.
    """
    order = SortOrder.DESC if field.startswith('-') else SortOrder.ASC
    return Sort(field.lstrip('-'), order), Sort('id')


class BaseService[ModelT: BaseModel]:
    """Чтение документов одного индекса с кешированием.

    Наследник задаёт индекс и модель документа, а сам описывает только запросы.
    Ответы кешируются по набору параметров запроса: одинаковые запросы
    не доходят до хранилища, пока запись в кеше не устарела.
    """

    index: ClassVar[str]
    model: ClassVar[type[BaseModel]]

    def __init__(self, storage: DocumentStorage, cache: ModelCache):
        self.storage = storage
        self.cache = cache

    async def _get_by_id(self, item_id: UUID) -> ModelT | None:
        """Возвращает документ по id или None, если его нет.

        Метод защищённый, а публичный `get_by_id` каждый наследник объявляет
        сам: у фильмов он принимает ещё и права доступа, и подменять им
        сигнатуру базового класса было бы нарушением контракта наследования.
        """
        cache_key = f'{self.index}:id:{item_id}'
        cached = await self.cache.get(cache_key, self.model)
        if cached is not None:
            # `model` объявлен как ClassVar и не может нести переменную типа:
            # ModelT известен наследнику, а базовому классу — только BaseModel.
            return cast(ModelT, cached)

        doc = await self.storage.get(self.index, str(item_id), fields=tuple(self.model.model_fields))
        if doc is None:
            return None

        item = cast(ModelT, self.model.model_validate(doc))
        await self.cache.set(cache_key, item, self.model)
        return item

    async def _search[ItemT: BaseModel](
        self,
        model: type[ItemT],
        pagination: Pagination,
        text: TextQuery | None = None,
        related_to: RelatedTo | None = None,
        filters: tuple[FieldIn, ...] = (),
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
            filters=filters,
            sort=sort,
        )

        cache_key = self._search_cache_key(request)
        # `list[model]` собирается из переменной, поэтому для проверяющего
        # типов это не тип, а значение: подсказываем ему явно.
        items_type = cast(type[list[ItemT]], list[model])  # type: ignore[valid-type]
        cached = await self.cache.get(cache_key, items_type)
        if cached is not None:
            return cached

        docs = await self.storage.search(self.index, request)
        items = [model.model_validate(doc) for doc in docs]
        await self.cache.set(cache_key, items, items_type)
        return items

    def _search_cache_key(self, request: SearchRequest) -> str:
        # md5 здесь не защищает, а даёт короткий отпечаток параметров для ключа кеша.
        digest = hashlib.md5(
            orjson.dumps(request, option=orjson.OPT_SORT_KEYS), usedforsecurity=False,
        ).hexdigest()
        return f'{self.index}:search:{digest}'
