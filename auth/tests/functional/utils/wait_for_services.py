"""Ждёт, пока начнут отвечать PostgreSQL, Redis и сам сервис.

Пауза между попытками растёт экспоненциально, но не дольше 5 секунд.
depends_on гарантирует только запуск процессов, а не их готовность.
"""

import asyncio

import asyncpg
import backoff
import httpx
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from tests.functional.settings import settings


def retry(*errors: type[Exception]):
    return backoff.on_exception(backoff.expo, errors, max_value=5, max_time=lambda: settings.wait_timeout)


@retry(OSError, asyncpg.PostgresError)
async def wait_for_postgres() -> None:
    connection = await asyncpg.connect(settings.postgres_dsn, timeout=2)
    await connection.close()


@retry(RedisConnectionError)
async def wait_for_redis() -> None:
    async with Redis(host=settings.redis_host, port=settings.redis_port, socket_connect_timeout=1) as client:
        await client.ping()


@retry(httpx.HTTPError)
async def wait_for_service() -> None:
    async with httpx.AsyncClient(base_url=settings.service_url, timeout=2) as client:
        (await client.get('/auth/api/openapi.json')).raise_for_status()


async def main() -> None:
    await asyncio.gather(wait_for_postgres(), wait_for_redis(), wait_for_service())


if __name__ == '__main__':
    asyncio.run(main())
