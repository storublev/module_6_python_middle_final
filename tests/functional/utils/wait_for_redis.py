"""Ждёт, пока Redis начнёт отвечать на ping()."""

import time

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from tests.functional.settings import settings


def wait_for_redis() -> None:
    client = Redis(host=settings.redis_host, port=settings.redis_port, socket_connect_timeout=1)
    deadline = time.monotonic() + settings.wait_timeout
    while True:
        try:
            if client.ping():
                break
        except (RedisConnectionError, RedisTimeoutError):
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f'Redis {settings.redis_host}:{settings.redis_port} '
                               f'не ответил за {settings.wait_timeout} с')
        time.sleep(1)
    client.close()


if __name__ == '__main__':
    wait_for_redis()
