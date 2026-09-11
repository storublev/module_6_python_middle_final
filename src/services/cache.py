import logging
from abc import ABC, abstractmethod

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class Cache(ABC):
    """Интерфейс кеша: сервисы не зависят от конкретного хранилища."""

    @abstractmethod
    async def get(self, key: str) -> bytes | None:
        """Возвращает значение по ключу или None, если его нет."""

    @abstractmethod
    async def set(self, key: str, value: bytes | str, expire: int) -> None:
        """Сохраняет значение на expire секунд."""


class RedisCache(Cache):
    """Кеш в Redis.

    Недоступность Redis не должна ронять API: при ошибке запрос уходит
    напрямую в Elasticsearch, а в лог пишется предупреждение.
    """

    def __init__(self, redis: Redis):
        self.redis = redis

    async def get(self, key: str) -> bytes | None:
        try:
            return await self.redis.get(key)
        except RedisError as exc:
            logger.warning('Не удалось прочитать ключ %s из Redis: %s', key, exc)
            return None

    async def set(self, key: str, value: bytes | str, expire: int) -> None:
        try:
            await self.redis.set(key, value, ex=expire)
        except RedisError as exc:
            logger.warning('Не удалось записать ключ %s в Redis: %s', key, exc)
