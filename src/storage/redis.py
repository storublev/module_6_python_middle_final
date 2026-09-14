import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from storage.cache import Cache

logger = logging.getLogger(__name__)


class RedisCache(Cache):
    """Кеш в Redis.

    Недоступность Redis не должна ронять API: при ошибке запрос уходит
    напрямую в хранилище документов, а в лог пишется предупреждение.
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
