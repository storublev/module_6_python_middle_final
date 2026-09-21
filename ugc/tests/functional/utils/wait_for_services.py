"""Ждёт, пока начнут отвечать Kafka и сам сервис.

Пауза между попытками растёт экспоненциально, но не дольше 5 секунд.
depends_on гарантирует только запуск процессов, а не их готовность; брокеру
к тому же нужно время на выборы контроллера.
"""

import backoff
import requests
from kafka import KafkaConsumer
from kafka.errors import KafkaError

from tests.functional.settings import settings


def retry(*errors: type[Exception]):
    return backoff.on_exception(backoff.expo, errors, max_value=5, max_time=lambda: settings.wait_timeout)


@retry(KafkaError, OSError)
def wait_for_kafka() -> None:
    consumer = KafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers.split(','),
        request_timeout_ms=3000,
    )
    try:
        # Список топиков заставляет клиента сходить за метаданными: важно не
        # содержимое списка, а что брокер вообще отвечает.
        consumer.topics()
    finally:
        consumer.close()


@retry(requests.RequestException)
def wait_for_service() -> None:
    response = requests.get(f'{settings.service_url}/ugc/api/v1/ready', timeout=2)
    response.raise_for_status()


def main() -> None:
    wait_for_kafka()
    wait_for_service()


if __name__ == '__main__':
    main()
