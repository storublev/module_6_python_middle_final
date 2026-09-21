"""Источник событий: топик Kafka.

Единственное место, где ETL знает про Kafka.

Здесь же собирается пачка. Это не украшение: `poll()` возвращает столько
сообщений, сколько уже лежит в буфере клиента, а `max_records` и `timeout_ms`
задают только верхнюю границу и время ожидания **появления** данных, а не
наполнения пачки. Отдавая наружу то, что вернул один `poll()`, ETL при редком
потоке вставлял бы в ClickHouse по десятку строк, а каждая вставка в MergeTree
— это новый кусок на диске и работа для слияний. Поэтому сообщения копятся,
пока не наберётся `batch_size` или не истечёт `batch_max_wait`.

Смещения подтверждаются вручную (`enable_auto_commit=False`) и **ровно те, что
уехали в хранилище**: автокоммит подтверждал бы по таймеру всё прочитанное, в
том числе сообщения, которые ещё лежат в накопителе. Ручное подтверждение даёт
at-least-once — пачка может повториться, но не пропасть; повторы убирает
ReplacingMergeTree в ClickHouse и дедупликация по `event_id` в запросах.
"""

import json
import logging
from collections.abc import Iterator, Sequence
from time import monotonic

from kafka import KafkaConsumer, OffsetAndMetadata, TopicPartition
from kafka.errors import KafkaError

from storage.base import EventSource, SourceUnavailableError

logger = logging.getLogger(__name__)


class KafkaEventSource(EventSource):
    """Чтение событий группой потребителей с накоплением пачки."""

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        *,
        auto_offset_reset: str = 'earliest',
        batch_size: int = 10_000,
        batch_max_wait: float = 30.0,
        poll_timeout: float = 5.0,
        consumer_factory=KafkaConsumer,
    ) -> None:
        self._batch_size = batch_size
        self._batch_max_wait = batch_max_wait
        self._poll_timeout_ms = int(poll_timeout * 1000)
        self._stopped = False
        # Смещения последней отданной пачки: подтверждаем только их.
        self._pending: dict[TopicPartition, int] = {}
        self._consumer = consumer_factory(
            topic,
            bootstrap_servers=bootstrap_servers.split(','),
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset=auto_offset_reset,
            max_poll_records=batch_size,
        )

    def batches(self) -> Iterator[Sequence[dict]]:
        """Копит сообщения и отдаёт их пачками, пока источник не остановят.

        Пачка уходит наружу, когда набралось `batch_size` событий или прошло
        `batch_max_wait` секунд — смотря что раньше. Второе условие нужно
        ночью, когда пачка не наберётся никогда, а задержка доставки события
        ограничена пятью минутами (НФТ-6).
        """
        while not self._stopped:
            events: list[dict] = []
            offsets: dict[TopicPartition, int] = {}
            deadline = monotonic() + self._batch_max_wait

            while not self._stopped and len(events) < self._batch_size and monotonic() < deadline:
                for partition, records in self._poll().items():
                    events.extend(self._decode(records))
                    # Подтверждать нужно смещение следующего сообщения.
                    offsets[partition] = records[-1].offset + 1
                if not events:
                    # Пустой топик: нет смысла крутиться до конца срока —
                    # отдаём пустую пачку, и вызывающий решит, что делать.
                    break

            self._pending = offsets
            yield events

    def commit(self) -> None:
        """Подтверждает смещения последней отданной пачки.

        Именно её, а не текущую позицию потребителя: за время накопления
        клиент мог прочитать вперёд, и подтверждение позиции потеряло бы
        сообщения, которые ещё не доехали до хранилища.
        """
        if not self._pending:
            return
        try:
            self._consumer.commit({
                partition: OffsetAndMetadata(offset, '', -1)
                for partition, offset in self._pending.items()
            })
        except KafkaError as error:
            raise SourceUnavailableError(str(error)) from error
        self._pending = {}

    def stop(self) -> None:
        """Просит цикл чтения завершиться после текущей пачки."""
        self._stopped = True

    def close(self) -> None:
        try:
            self._consumer.close()
        except KafkaError as error:
            logger.warning('Потребитель Kafka закрылся с ошибкой: %s', error)

    def _poll(self) -> dict:
        try:
            return self._consumer.poll(timeout_ms=self._poll_timeout_ms, max_records=self._batch_size)
        except KafkaError as error:
            raise SourceUnavailableError(str(error)) from error

    @staticmethod
    def _decode(records) -> Iterator[dict]:
        """Разбирает сообщения, пропуская те, что не разбираются.

        Сообщение, которое не читается, остановило бы перенос навсегда: ETL
        падал бы на нём, начинал с того же смещения и падал снова. Поэтому
        такое сообщение пропускается с записью в журнал — потеря одного
        события укладывается в допустимые 0,1% (НФТ-4), а остановка переноса
        не укладывается никуда.
        """
        for record in records:
            try:
                yield json.loads(record.value)
            except (ValueError, TypeError):
                logger.warning(
                    'Сообщение не разобрано и пропущено: раздел %s, смещение %s',
                    record.partition, record.offset,
                )
