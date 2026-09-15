from redis.asyncio import Redis

redis: Redis | None = None


async def get_redis() -> Redis:
    """Возвращает клиент Redis, созданный при старте приложения."""
    return redis
