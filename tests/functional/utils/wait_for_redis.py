"""Ждёт, пока Redis начнёт отвечать на ping().

Пауза между попытками растёт экспоненциально, но не дольше 5 секунд.
"""

import backoff
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from tests.functional.settings import settings


@backoff.on_exception(
    backoff.expo,
    (RedisConnectionError, RedisTimeoutError),
    max_value=5,
    max_time=lambda: settings.wait_timeout,
)
def ping(client: Redis) -> bool:
    return client.ping()


def wait_for_redis() -> None:
    with Redis(host=settings.redis_host, port=settings.redis_port, socket_connect_timeout=1) as client:
        ping(client)


if __name__ == '__main__':
    wait_for_redis()
