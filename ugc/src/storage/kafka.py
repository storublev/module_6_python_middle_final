"""Очередь событий на Kafka.

Единственное место, где сервис знает про Kafka. Почему именно Kafka — ADR-2 в
docs/architecture/README.md; почему клиент чистый питоновский, а не обёртка над
librdkafka, — ADR-3: под gevent блокирующий вызов в C-библиотеку остановил бы
весь процесс, а сокеты стандартной библиотеки gevent подменяет и переключает
зелёные потоки сам.
"""

import logging
from collections.abc import Sequence

from kafka import KafkaProducer
from kafka.errors import KafkaError

from storage.base import EventQueue, QueuedEvent, QueueUnavailableError

logger = logging.getLogger(__name__)


class KafkaEventQueue(EventQueue):
    """Отправка событий в топик Kafka с подтверждением записи.

    Продюсер один на процесс: он держит соединения с брокерами, знает
    метаданные топика и копит сообщения в своём буфере. Создавать его на
    запрос значило бы заново открывать соединения по нескольку сотен раз в
    секунду.

    `acks='all'` — ждём записи во все синхронизированные реплики: пик гасит
    брокер, и терять события на его стороне незачем. Плата за это — задержка
    подтверждения, но она укладывается в отведённые ответу 100 мс.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        *,
        acks: str = 'all',
        linger_ms: int = 10,
        batch_size: int = 64 * 1024,
        compression_type: str | None = 'lz4',
        request_timeout_ms: int = 5000,
        max_block_ms: int = 5000,
        retries: int = 3,
        flush_timeout: float = 5.0,
        producer_factory=KafkaProducer,
    ) -> None:
        self._topic = topic
        self._flush_timeout = flush_timeout
        self._producer = producer_factory(
            bootstrap_servers=bootstrap_servers.split(','),
            acks=acks,
            # Продюсер ждёт несколько миллисекунд и склеивает сообщения в один
            # запрос к брокеру: на нашем потоке это заметно дешевле, чем
            # отправлять каждое событие отдельно.
            linger_ms=linger_ms,
            batch_size=batch_size,
            compression_type=compression_type,
            request_timeout_ms=request_timeout_ms,
            # Сколько ждать места в буфере и метаданных топика, прежде чем
            # признать брокер недоступным. Без предела send() ждал бы вечно,
            # а вместе с ним ждал бы и клиент.
            max_block_ms=max_block_ms,
            retries=retries,
            key_serializer=lambda key: key.encode(),
        )

    def publish(self, events: Sequence[QueuedEvent]) -> None:
        """Отправляет пачку и ждёт, пока брокер подтвердит запись всех событий."""
        try:
            for event in events:
                self._producer.send(self._topic, key=event.key, value=event.value)
            # flush() возвращается, когда подтверждены все отправленные
            # сообщения: только после этого клиенту можно отвечать «принято».
            self._producer.flush(timeout=self._flush_timeout)
        except KafkaError as error:
            logger.warning('Не удалось записать %d событий в Kafka: %s', len(events), error)
            raise QueueUnavailableError(str(error)) from error

    def is_ready(self) -> bool:
        """Знает ли продюсер партиции своего топика.

        Не `bootstrap_connected()`: тот отвечает только про соединение с
        адресом из списка bootstrap, а после первого запроса метаданных
        клиент переключается на настоящие брокеры и это соединение закрывает —
        проверка начинает врать. Список партиций же означает ровно то, что
        нужно: брокеры отвечают и топик существует.
        """
        try:
            return bool(self._producer.partitions_for(self._topic))
        except KafkaError:
            return False

    def close(self) -> None:
        """Дописывает буфер и закрывает соединения.

        Вызывается при остановке воркера: события, которые успели попасть в
        буфер, но ещё не ушли, иначе пропали бы вместе с процессом.
        """
        try:
            self._producer.close(timeout=self._flush_timeout)
        except KafkaError as error:
            logger.warning('Продюсер Kafka закрылся с ошибкой: %s', error)
