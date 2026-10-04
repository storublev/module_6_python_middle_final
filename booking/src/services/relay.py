"""Ретранслятор outbox: переносит события о бронях в сервис уведомлений.

Вторая половина транзакционного outbox (ADR-23). Бронь и событие для писем
записаны одной транзакцией; ретранслятор забирает события, отправляет их в
API уведомлений и только потом удаляет.

Гарантия — at-least-once: упади ретранслятор между отправкой и удалением,
событие уйдёт ещё раз. Это безопасно — `event_id` у события постоянный (это
идентификатор строки outbox), и сервис уведомлений повтор не превратит во
второе письмо.

Сервис уведомлений лежит — событие откладывается на паузу повтора и ждёт в
базе. Гость об этом не узнает: бронь уже сделана.
"""

import logging
from datetime import UTC, datetime, timedelta

from storage.base import EventRejectedError, NotificationGateway, Outbox, StorageUnavailableError

logger = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(
        self, outbox: Outbox, gateway: NotificationGateway, batch_size: int, lease: timedelta, retry_delay: timedelta,
    ) -> None:
        self._outbox = outbox
        self._gateway = gateway
        self._batch_size = batch_size
        self._lease = lease
        self._retry_delay = retry_delay

    async def relay_once(self, now: datetime | None = None) -> int:
        """Отправляет события, которым пора. Возвращает число отправленных."""
        moment = now or datetime.now(UTC)
        sent = 0
        for message in await self._outbox.claim(self._batch_size, self._lease, moment):
            try:
                await self._gateway.send(message.id, message.payload, message.request_id)
            except StorageUnavailableError as error:
                # Сервис не принял — остальные события пачки упрутся в то же.
                # Они вернутся сами, когда выйдет аренда.
                await self._outbox.retry(message.id, moment + self._retry_delay, str(error))
                logger.warning(
                    'Сервис уведомлений не принял событие, повтор через %s: %s', self._retry_delay, error,
                    extra={'event_id': str(message.id), 'attempt': message.attempts},
                )
                break
            except EventRejectedError as error:
                # Отказ по существу (нет шаблона, неверный формат) повтором не
                # лечится: событие снимается, а ошибка уходит в журнал и Sentry —
                # это дефект, который надо чинить в коде, а не ждать.
                logger.error('Сервис уведомлений отверг событие: %s', error, extra={'event_id': str(message.id)})
            else:
                sent += 1
            await self._outbox.done(message.id)
        return sent
