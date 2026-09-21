"""Адаптер Kafka: что уходит в продюсер и как наружу выходят его ошибки.

Настоящий брокер здесь не нужен — проверяется договор адаптера с библиотекой:
KafkaProducer подменяется заглушкой, которая записывает вызовы.
"""

import pytest
from kafka.errors import BrokerNotAvailableError, KafkaTimeoutError

from storage.base import QueuedEvent, QueueUnavailableError
from storage.kafka import KafkaEventQueue

TOPIC = 'ugc.events'
EVENTS = [QueuedEvent(key='session-1', value=b'{"event_type":"click"}')]


class FakeProducer:
    """Продюсер, записывающий вызовы вместо обращения к брокеру."""

    def __init__(self, *, send_error: Exception | None = None, flush_error: Exception | None = None, **kwargs):
        self.kwargs = kwargs
        self.sent: list[tuple[str, str, bytes]] = []
        self.flushed = False
        self.closed = False
        self.connected = True
        self._send_error = send_error
        self._flush_error = flush_error

    def send(self, topic, key=None, value=None):
        if self._send_error:
            raise self._send_error
        self.sent.append((topic, key, value))

    def flush(self, timeout=None):
        if self._flush_error:
            raise self._flush_error
        self.flushed = True

    def partitions_for(self, topic):
        return {0, 1} if self.connected else set()

    def close(self, timeout=None):
        self.closed = True


def make_queue(**producer_kwargs) -> tuple[KafkaEventQueue, FakeProducer]:
    producer = FakeProducer(**producer_kwargs)
    queue = KafkaEventQueue('kafka:9092', TOPIC, producer_factory=lambda **kwargs: producer)
    return queue, producer


def test_events_go_to_the_configured_topic() -> None:
    """События уходят в заданный топик с ключом партиционирования."""
    queue, producer = make_queue()

    queue.publish(EVENTS)

    assert producer.sent == [(TOPIC, 'session-1', EVENTS[0].value)]


def test_publish_waits_for_acknowledgement() -> None:
    """Метод возвращается только после подтверждения записи: иначе клиенту нельзя отвечать «принято»."""
    queue, producer = make_queue()

    queue.publish(EVENTS)

    assert producer.flushed


def test_send_failure_becomes_queue_unavailable() -> None:
    """Ошибка библиотеки при отправке выходит наружу контрактным исключением."""
    queue, _ = make_queue(send_error=BrokerNotAvailableError())

    with pytest.raises(QueueUnavailableError):
        queue.publish(EVENTS)


def test_flush_timeout_becomes_queue_unavailable() -> None:
    """Не дождались подтверждения — для вызывающего это та же недоступность очереди."""
    queue, _ = make_queue(flush_error=KafkaTimeoutError())

    with pytest.raises(QueueUnavailableError):
        queue.publish(EVENTS)


def test_readiness_follows_the_metadata() -> None:
    """Готовность адаптера — это знание партиций топика: брокеры отвечают и топик есть."""
    queue, producer = make_queue()
    assert queue.is_ready()

    producer.connected = False
    assert not queue.is_ready()


def test_close_flushes_the_buffer() -> None:
    """При остановке продюсер закрывается: события из его буфера не должны пропасть."""
    queue, producer = make_queue()

    queue.close()

    assert producer.closed
