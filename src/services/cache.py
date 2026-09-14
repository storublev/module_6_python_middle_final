import logging
from functools import cache
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError

from storage.cache import Cache

logger = logging.getLogger(__name__)

T = TypeVar('T')


@cache
def type_adapter(tp: Any) -> TypeAdapter[Any]:
    """TypeAdapter дорого создавать, поэтому он строится один раз на тип."""
    return TypeAdapter(tp)


class ModelCache:
    """Кеш моделей pydantic поверх любого хранилища Cache.

    Отвечает за то, как ответы сервисов лежат в кеше: сериализует их в JSON,
    при чтении проверяет запись и задаёт время жизни. Сервисы решают только,
    что и под каким ключом кешировать.
    """

    def __init__(self, storage: Cache, expire: int):
        self.storage = storage
        self.expire = expire

    async def get(self, key: str, tp: type[T]) -> T | None:
        """Читает запись типа tp; повреждённую запись считает отсутствующей.

        Запись может оказаться некорректным JSON или перестать соответствовать
        модели после обновления приложения. Тогда сервис прочитает данные из
        хранилища документов и перезапишет запись свежей.
        """
        cached = await self.storage.get(key)
        if cached is None:
            return None
        try:
            return type_adapter(tp).validate_json(cached)
        except ValidationError as exc:
            logger.warning('Запись %s в кеше не прошла проверку, читаем из хранилища: %s', key, exc)
            return None

    async def set(self, key: str, value: T, tp: type[T]) -> None:
        await self.storage.set(key, type_adapter(tp).dump_json(value), self.expire)
