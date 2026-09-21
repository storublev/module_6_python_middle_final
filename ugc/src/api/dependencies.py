"""Composition Root: единственное место, где выбираются реализации.

Обработчики и бизнес-логика работают с интерфейсами (`EventQueue`), а то,
что за ними стоит Kafka, известно только здесь. В тестах сюда же
подставляется очередь в памяти — код сервиса при этом не меняется.

Объекты создаются один раз на процесс-воркер и живут, пока он жив: продюсер
Kafka держит соединения с брокерами, и создавать его на каждый запрос значило
бы открывать их по нескольку сотен раз в секунду.
"""

from dataclasses import dataclass

from flask import Flask, current_app

from api.security import TokenVerifier
from core.config import Settings
from services.collector import EventCollector
from storage.base import EventQueue
from storage.kafka import KafkaEventQueue

EXTENSION_KEY = 'ugc'


@dataclass(frozen=True)
class Services:
    """Всё, что нужно обработчикам, собрано в одном месте."""

    collector: EventCollector
    queue: EventQueue
    verifier: TokenVerifier
    max_events_per_request: int


def build_services(settings: Settings, queue: EventQueue | None = None) -> Services:
    """Собирает сервисы по настройкам. `queue` подменяется в тестах."""
    event_queue = queue or KafkaEventQueue(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=settings.kafka_topic,
        acks=settings.kafka_acks,
        linger_ms=settings.kafka_linger_ms,
        batch_size=settings.kafka_batch_size,
        compression_type=settings.kafka_compression,
        request_timeout_ms=settings.kafka_request_timeout_ms,
        max_block_ms=settings.kafka_max_block_ms,
        retries=settings.kafka_retries,
        flush_timeout=settings.kafka_flush_timeout,
    )
    return Services(
        collector=EventCollector(event_queue),
        queue=event_queue,
        verifier=TokenVerifier(settings.jwt_secret_key.get_secret_value(), settings.jwt_algorithm),
        max_events_per_request=settings.max_events_per_request,
    )


def register_services(app: Flask, services: Services) -> None:
    app.extensions[EXTENSION_KEY] = services


def get_services() -> Services:
    return current_app.extensions[EXTENSION_KEY]
