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

Сервис уведомлений отверг событие по существу (4xx: нет шаблона, разошёлся
формат после обновления одного из сервисов) — повтор сам по себе не поможет,
но и удалять событие нельзя: в нём единственная копия данных письма о брони
или отмене. Событие откладывается с причиной отказа (`rejected_at`), обычная
отправка его больше не берёт, а после исправления его возвращают в очередь
командой `python outbox_cli.py requeue` — с тем же event_id.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from models.domain import RejectedEvent
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
                # Отказ по существу повтором не лечится — это дефект, который
                # чинят в коде. Ошибка уходит в журнал и Sentry, а событие
                # откладывается до исправления, а не теряется.
                await self._outbox.reject(message.id, moment, str(error))
                logger.error(
                    'Сервис уведомлений отверг событие, оно отложено до исправления: %s', error,
                    extra={'event_id': str(message.id)},
                )
                continue
            sent += 1
            await self._outbox.done(message.id)
        return sent


class RejectedEvents:
    """Разбор отклонённых событий после исправления: посмотреть и вернуть в отправку."""

    def __init__(self, outbox: Outbox) -> None:
        self._outbox = outbox

    async def list(self, limit: int) -> list[RejectedEvent]:
        return await self._outbox.rejected(limit)

    async def requeue(self, message_ids: Sequence[UUID] | None = None, now: datetime | None = None) -> int:
        return await self._outbox.requeue(message_ids, now or datetime.now(UTC))
