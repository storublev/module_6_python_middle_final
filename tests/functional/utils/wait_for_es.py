"""Ждёт, пока Elasticsearch начнёт отвечать.

depends_on в docker-compose проверяет только запуск процесса в контейнере,
а не готовность сервиса, поэтому перед тестами Elasticsearch опрашивается ping().
"""

import time

from elasticsearch import Elasticsearch

from tests.functional.settings import settings


def wait_for_es() -> None:
    client = Elasticsearch(hosts=settings.elastic_url)
    deadline = time.monotonic() + settings.wait_timeout
    while not client.ping():
        if time.monotonic() > deadline:
            raise TimeoutError(f'Elasticsearch {settings.elastic_url} не ответил за {settings.wait_timeout} с')
        time.sleep(1)
    client.close()


if __name__ == '__main__':
    wait_for_es()
