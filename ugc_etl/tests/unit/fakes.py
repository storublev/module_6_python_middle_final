"""Источник и приёмник в памяти с тем же контрактом, что у Kafka и ClickHouse.

Перенос зависит только от интерфейсов `storage/base.py`, поэтому его логику —
порядок вставки и подтверждения, поведение при сбоях — можно проверить без
брокера и без хранилища.
"""

from collections.abc import Iterator, Sequence
from typing import Any

from storage.base import (
    EventSink,
    EventSource,
    SinkDataError,
    SinkUnavailableError,
    SourceUnavailableError,
)


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
    """Складывает строки в список.

    Умеет изображать обе беды хранилища: временную недоступность (`failures`
    отказов подряд) и негодные данные (`broken` — значения первой колонки,
    которые хранилище не принимает никогда).
    """

    def __init__(self, *, failures: int = 0, broken: set | None = None) -> None:
        self.rows: list[Sequence[Any]] = []
        self.inserts = 0
        self.failures = failures
        self.broken = broken or set()

    def insert(self, rows: Sequence[Sequence[Any]]) -> None:
        self.inserts += 1
        if self.failures:
            self.failures -= 1
            raise SinkUnavailableError('хранилище недоступно')
        rejected = [row for row in rows if row and row[0] in self.broken]
        if rejected:
            raise SinkDataError(f'значение не помещается в колонку: {rejected[0][0]}')
        self.rows.extend(rows)

    def close(self) -> None:
        pass
