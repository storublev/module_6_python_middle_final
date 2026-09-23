from redis.asyncio import Redis
from redis.exceptions import RedisError

from storage.cache import Cache, CacheUnavailableError


class RedisCache(Cache):
    """Кеш в Redis. Сбои Redis переводит в CacheUnavailableError."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def get(self, key: str) -> bytes | None:
        try:
            # Клиент создан без decode_responses, поэтому приходят байты, но в
            # типах библиотеки значение объявлено шире — bytes | str | None.
            value = await self.redis.get(key)
            return value.encode() if isinstance(value, str) else value
        except RedisError as exc:
            raise CacheUnavailableError(f'Redis: {exc}') from exc

    async def set(self, key: str, value: bytes | str, expire: int) -> None:
        try:
            await self.redis.set(key, value, ex=expire)
        except RedisError as exc:
            raise CacheUnavailableError(f'Redis: {exc}') from exc
