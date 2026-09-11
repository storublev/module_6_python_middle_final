from dataclasses import dataclass
from typing import Any, ClassVar, Generic, TypeVar
from uuid import UUID

from elasticsearch import AsyncElasticsearch, NotFoundError
from pydantic import BaseModel

ModelT = TypeVar('ModelT', bound=BaseModel)
ItemT = TypeVar('ItemT', bound=BaseModel)


@dataclass(frozen=True)
class Pagination:
    page_number: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page_number - 1) * self.page_size


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
    """Чтение документов одного индекса Elasticsearch.

    Наследник задаёт индекс и модель документа, а сам описывает только запросы.
    """

    index: ClassVar[str]
    model: ClassVar[type[BaseModel]]

    def __init__(self, elastic: AsyncElasticsearch):
        self.elastic = elastic

    async def get_by_id(self, item_id: UUID) -> ModelT | None:
        """Возвращает документ по id или None, если его нет."""
        try:
            doc = await self.elastic.get(
                index=self.index,
                id=str(item_id),
                source_includes=list(self.model.model_fields),
            )
        except NotFoundError:
            return None
        return self.model.model_validate(doc['_source'])

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

        try:
            response = await self.elastic.search(index=self.index, track_total_hits=False, **params)
        except NotFoundError:
            # Индекс ещё не создан ETL — это пустой результат, а не ошибка сервера.
            return []

        return [model.model_validate(hit['_source']) for hit in response['hits']['hits']]
