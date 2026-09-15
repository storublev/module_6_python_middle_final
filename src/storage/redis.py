from redis.asyncio import Redis
from redis.exceptions import RedisError

from storage.cache import Cache, CacheUnavailableError


class RedisCache(Cache):
    """Кеш в Redis. Сбои Redis переводит в CacheUnavailableError."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def get(self, key: str) -> bytes | None:
        try:
            return await self.redis.get(key)
        except RedisError as exc:
            raise CacheUnavailableError(f'Redis: {exc}') from exc

    async def set(self, key: str, value: bytes | str, expire: int) -> None:
        try:
            await self.redis.set(key, value, ex=expire)
        except RedisError as exc:
            raise CacheUnavailableError(f'Redis: {exc}') from exc
