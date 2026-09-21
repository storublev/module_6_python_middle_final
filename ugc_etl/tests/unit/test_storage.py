"""Адаптеры Kafka и ClickHouse: что уходит в библиотеки и как выходят их ошибки.

Настоящие брокер и хранилище здесь не нужны — проверяется договор адаптера с
библиотекой, поэтому клиенты подменены заглушками.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError
from kafka.errors import BrokerNotAvailableError

from storage.base import SinkUnavailableError, SourceUnavailableError
from storage.clickhouse import ClickHouseEventSink
from storage.kafka import KafkaEventSource

TOPIC = 'ugc.events'
ROWS = [(uuid4(), 'click', datetime.now(UTC))]


class FakeRecord:
    def __init__(self, value: bytes, partition: int = 0, offset: int = 0):
        self.value = value
        self.partition = partition
        self.offset = offset


class FakeConsumer:
    """Потребитель, отдающий заранее заданные ответы poll()."""

    def __init__(self, polls=(), *, poll_error=None, commit_error=None):
        # Сюда адаптер кладёт настройки, с которыми создал потребителя.
        self.kwargs: dict = {}
        self._polls = list(polls)
        self._poll_error = poll_error
        self._commit_error = commit_error
        self.commits = 0
        self.closed = False

    def poll(self, timeout_ms=None, max_records=None):
        if self._poll_error:
            raise self._poll_error
        return self._polls.pop(0) if self._polls else {}

    def commit(self):
        if self._commit_error:
            raise self._commit_error
        self.commits += 1

    def close(self):
        self.closed = True


def make_source(polls=(), **fake_kwargs) -> tuple[KafkaEventSource, FakeConsumer]:
    consumer = FakeConsumer(polls, **fake_kwargs)

    def factory(*args, **kwargs) -> FakeConsumer:
        consumer.kwargs = kwargs
        return consumer

    return KafkaEventSource('kafka:9092', TOPIC, 'ugc-etl', consumer_factory=factory), consumer


def encoded(**fields) -> FakeRecord:
    return FakeRecord(json.dumps(fields).encode())


def test_source_decodes_messages() -> None:
    """Сообщения приходят разобранными из JSON."""
    source, _ = make_source([{'partition-0': [encoded(event_type='click')]}])

    batch = next(iter(source.batches()))

    assert batch == [{'event_type': 'click'}]


def test_source_skips_unreadable_message() -> None:
    """Нечитаемое сообщение пропускается: иначе ETL падал бы на нём вечно."""
    source, _ = make_source([{'partition-0': [FakeRecord(b'{not json'), encoded(event_type='click')]}])

    batch = next(iter(source.batches()))

    assert batch == [{'event_type': 'click'}]


def test_source_offsets_are_not_committed_automatically() -> None:
    """Автокоммит выключен: смещения подтверждает ETL после вставки."""
    _, consumer = make_source()

    assert consumer.kwargs['enable_auto_commit'] is False


def test_source_failure_becomes_contract_error() -> None:
    """Ошибка библиотеки при чтении выходит наружу контрактным исключением."""
    source, _ = make_source(poll_error=BrokerNotAvailableError())

    with pytest.raises(SourceUnavailableError):
        next(iter(source.batches()))


def test_commit_failure_becomes_contract_error() -> None:
    """Ошибка подтверждения смещений тоже приводится к контракту."""
    source, _ = make_source(commit_error=BrokerNotAvailableError())

    with pytest.raises(SourceUnavailableError):
        source.commit()


def test_stop_ends_the_stream() -> None:
    """Остановка прекращает поток пачек."""
    source, _ = make_source()
    source.stop()

    assert list(source.batches()) == []


class FakeClient:
    """Клиент ClickHouse, записывающий вызовы вместо обращения к серверу."""

    def __init__(self, *, error=None, **kwargs):
        self.kwargs = kwargs
        self.inserted: list = []
        self.closed = False
        self._error = error

    def insert(self, table, rows, column_names=None, column_type_names=None, database=None):
        if self._error:
            raise self._error
        self.inserted.append((table, rows, column_names, column_type_names, database))

    def close(self):
        self.closed = True


def make_sink(**client_kwargs) -> tuple[ClickHouseEventSink, FakeClient]:
    client = FakeClient(**client_kwargs)
    sink = ClickHouseEventSink(
        'clickhouse', 8123, 'default', '', 'ugc', 'events',
        client_factory=lambda **kwargs: client,
    )
    return sink, client


def test_sink_inserts_rows_with_explicit_types() -> None:
    """Типы колонок передаются явно: иначе драйвер выведет их по первой строке."""
    sink, client = make_sink()

    sink.insert(ROWS)

    table, rows, names, types, database = client.inserted[0]
    assert (table, database, rows) == ('events', 'ugc', ROWS)
    assert names and types


def test_sink_does_nothing_on_empty_batch() -> None:
    """Пустая пачка до хранилища не доезжает: лишний кусок в MergeTree ни к чему."""
    sink, client = make_sink()

    sink.insert([])

    assert client.inserted == []


def test_sink_failure_becomes_contract_error() -> None:
    """Ошибка драйвера выходит наружу контрактным исключением."""
    sink, _ = make_sink(error=DatabaseError('слишком много кусков'))

    with pytest.raises(SinkUnavailableError):
        sink.insert(ROWS)


def test_lost_connection_becomes_contract_error() -> None:
    """Обрыв соединения драйвер отдаёт ошибкой сокета — она тоже приводится к контракту."""
    sink, _ = make_sink(error=ConnectionResetError('соединение сброшено'))

    with pytest.raises(SinkUnavailableError):
        sink.insert(ROWS)
