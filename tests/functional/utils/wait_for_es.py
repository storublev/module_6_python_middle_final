"""Ждёт, пока Elasticsearch начнёт отвечать.

depends_on в docker-compose проверяет только запуск процесса в контейнере,
а не готовность сервиса, поэтому перед тестами Elasticsearch опрашивается ping().
Пауза между попытками растёт экспоненциально, но не дольше 5 секунд.
"""

import backoff
from elasticsearch import Elasticsearch

from tests.functional.settings import settings


@backoff.on_predicate(backoff.expo, max_value=5, max_time=lambda: settings.wait_timeout)
def ping(client: Elasticsearch) -> bool:
    return client.ping()


def wait_for_es() -> None:
    with Elasticsearch(hosts=settings.elastic_url) as client:
        if not ping(client):
            raise TimeoutError(f'Elasticsearch {settings.elastic_url} не ответил за {settings.wait_timeout} с')


if __name__ == '__main__':
    wait_for_es()
