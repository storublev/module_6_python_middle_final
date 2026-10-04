from redis.asyncio import Redis

redis: Redis | None = None


async def get_redis() -> Redis:
    """Возвращает клиент Redis, созданный при старте приложения.

    Raises:
        RuntimeError: клиент не создан — значит, приложение не прошло lifespan.
    """
    if redis is None:
        raise RuntimeError('Клиент Redis не создан')
    return redis
