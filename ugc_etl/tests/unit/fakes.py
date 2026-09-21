"""Источник и приёмник в памяти с тем же контрактом, что у Kafka и ClickHouse.

Перенос зависит только от интерфейсов `storage/base.py`, поэтому его логику —
порядок вставки и подтверждения, поведение при сбоях — можно проверить без
брокера и без хранилища.
"""

from collections.abc import Iterator, Sequence
from typing import Any

from storage.base import EventSink, EventSource, SinkUnavailableError, SourceUnavailableError


class FakeSource(EventSource):
    """Отдаёт заранее заданные пачки и считает подтверждения."""

    def __init__(self, batches: Sequence[Sequence[dict]], *, commit_fails: bool = False) -> None:
        self._batches = list(batches)
        self.commits = 0
        self.commit_fails = commit_fails
        self.closed = False

    def batches(self) -> Iterator[Sequence[dict]]:
        yield from self._batches

    def commit(self) -> None:
        if self.commit_fails:
            raise SourceUnavailableError('брокер не подтвердил смещения')
        self.commits += 1

    def close(self) -> None:
        self.closed = True


class FakeSink(EventSink):
    """Складывает строки в список; умеет отказывать заданное число раз подряд."""

    def __init__(self, *, failures: int = 0) -> None:
        self.rows: list[Sequence[Any]] = []
        self.inserts = 0
        self.failures = failures

    def insert(self, rows: Sequence[Sequence[Any]]) -> None:
        self.inserts += 1
        if self.failures:
            self.failures -= 1
            raise SinkUnavailableError('хранилище недоступно')
        self.rows.extend(rows)

    def close(self) -> None:
        pass
