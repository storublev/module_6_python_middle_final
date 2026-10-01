"""Ретранслятор outbox: переносит задания из базы в RabbitMQ.

Вторая половина транзакционного outbox. API и генератор рассылок пишут
задания в базу вместе с данными; ретранслятор забирает их, публикует с
подтверждением брокера и только потом удаляет.

Гарантия — at-least-once: если ретранслятор упадёт между публикацией и
удалением, задание опубликуется ещё раз. Это безопасно: дальше по конвейеру
стоят ключи идемпотентности, и второго письма не будет (ADR-11).

Брокер лежит — задание откладывается на паузу повтора и ждёт в базе. Ничего
не теряется, и вызывающий об этом даже не узнаёт: событие уже принято.
"""

import logging
from datetime import datetime, timedelta, timezone

from storage.base import MessagePublisher, Outbox, StorageUnavailableError

logger = logging.getLogger(__name__)


class OutboxRelay:
    """Публикация накопленных заданий."""

    def __init__(
        self,
        outbox: Outbox,
        publisher: MessagePublisher,
        batch_size: int,
        lease: timedelta,
        retry_delay: timedelta,
    ) -> None:
        self._outbox = outbox
        self._publisher = publisher
        self._batch_size = batch_size
        self._lease = lease
        self._retry_delay = retry_delay

    async def relay_once(self, now: datetime | None = None) -> int:
        """Публикует задания, которым пора. Возвращает число опубликованных."""
        moment = now or datetime.now(timezone.utc)
        published = 0
        for message in await self._outbox.claim(self._batch_size, self._lease, moment):
            try:
                await self._publisher.publish(message.stage, message.payload, message.request_id)
            except StorageUnavailableError as error:
                # Брокер не принял — дальше пробовать бессмысленно: остальные
                # задания этой пачки упрутся в то же. Они вернутся сами, когда
                # выйдет аренда, а это — через паузу повтора.
                await self._outbox.retry(message.id, moment + self._retry_delay, str(error))
                logger.warning(
                    'Брокер не принял задание, повтор через %s: %s', self._retry_delay, error,
                    extra={'stage': message.stage, 'attempt': message.attempts},
                )
                break
            await self._outbox.done(message.id)
            published += 1
        return published

    @property
    def batch_size(self) -> int:
        return self._batch_size
