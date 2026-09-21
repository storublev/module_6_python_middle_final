"""Очередь в памяти с тем же контрактом, что у Kafka.

Бизнес-логика зависит только от интерфейса `storage/base.py`, поэтому её можно
проверить без брокера: тесты смотрят, что именно ушло бы в Kafka, и умеют
изображать её недоступность.
"""

from collections.abc import Sequence

from storage.base import EventQueue, QueuedEvent, QueueUnavailableError


class InMemoryEventQueue(EventQueue):
    """Складывает события в список вместо брокера."""

    def __init__(self, *, available: bool = True, ready: bool = True) -> None:
        self.published: list[QueuedEvent] = []
        self.available = available
        self.ready = ready
        self.closed = False

    def publish(self, events: Sequence[QueuedEvent]) -> None:
        if not self.available:
            raise QueueUnavailableError('брокер недоступен')
        self.published.extend(events)

    def is_ready(self) -> bool:
        return self.ready

    def close(self) -> None:
        self.closed = True
