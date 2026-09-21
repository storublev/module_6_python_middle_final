"""Источник событий: топик Kafka.

Единственное место, где ETL знает про Kafka.

Смещения подтверждаются вручную, после успешной вставки (`enable_auto_commit=False`):
автокоммит подтверждал бы прочитанное по таймеру, и падение между чтением и
вставкой стоило бы пачки событий. Ручное подтверждение даёт at-least-once —
пачка может повториться, но не пропасть; повторы убирает ReplacingMergeTree в
ClickHouse и дедупликация по `event_id` в запросах.
"""

import json
import logging
from collections.abc import Iterator, Sequence

from kafka import KafkaConsumer
from kafka.errors import KafkaError

from storage.base import EventSource, SourceUnavailableError

logger = logging.getLogger(__name__)


class KafkaEventSource(EventSource):
    """Чтение событий группой потребителей."""

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        *,
        auto_offset_reset: str = 'earliest',
        batch_size: int = 10_000,
        poll_timeout: float = 5.0,
        consumer_factory=KafkaConsumer,
    ) -> None:
        self._batch_size = batch_size
        self._poll_timeout_ms = int(poll_timeout * 1000)
        self._stopped = False
        self._consumer = consumer_factory(
            topic,
            bootstrap_servers=bootstrap_servers.split(','),
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset=auto_offset_reset,
            max_poll_records=batch_size,
        )

    def batches(self) -> Iterator[Sequence[dict]]:
        """Отдаёт пачки событий, пока источник не остановят."""
        while not self._stopped:
            try:
                records = self._consumer.poll(timeout_ms=self._poll_timeout_ms, max_records=self._batch_size)
            except KafkaError as error:
                raise SourceUnavailableError(str(error)) from error
            yield [event for partition in records.values() for event in self._decode(partition)]

    def commit(self) -> None:
        """Подтверждает смещения последней пачки."""
        try:
            self._consumer.commit()
        except KafkaError as error:
            raise SourceUnavailableError(str(error)) from error

    def stop(self) -> None:
        """Просит цикл чтения завершиться после текущей пачки."""
        self._stopped = True

    def close(self) -> None:
        try:
            self._consumer.close()
        except KafkaError as error:
            logger.warning('Потребитель Kafka закрылся с ошибкой: %s', error)

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
