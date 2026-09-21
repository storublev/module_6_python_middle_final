"""Приём событий: проверка контракта, обогащение и отправка в очередь.

Слой бизнес-логики. Не знает ни про HTTP, ни про Kafka: на вход получает
разобранный JSON и идентификатор пользователя, на выход отдаёт, сколько
событий принято и какие отклонены.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from models.events import EVENT_ADAPTER
from storage.base import EventQueue, QueuedEvent

logger = logging.getLogger(__name__)

# Код ошибки для события, не прошедшего проверку контракта. Он машиночитаемый:
# клиент по нему понимает, что повторять такое событие бессмысленно.
INVALID_EVENT = 'invalid_event'


@dataclass(frozen=True)
class RejectedEvent:
    """Отклонённое событие: его место в пачке и причина отказа."""

    index: int
    code: str
    detail: str


@dataclass(frozen=True)
class CollectResult:
    """Итог приёма пачки: сколько событий ушло в очередь и что отклонено."""

    accepted: int
    rejected: list[RejectedEvent]


class EventCollector:
    """Принимает пачку событий и складывает их в очередь.

    Каждое событие проверяется отдельно: испорченное не уносит с собой
    остальные (ФТ-6). В очередь пачка уходит целиком и за один раз — либо
    записаны все проверенные события, либо ни одного, и тогда клиент повторит
    запрос. Частичная запись была бы хуже: клиент не узнал бы, что повторять.
    """

    def __init__(self, queue: EventQueue, clock=lambda: datetime.now(UTC)) -> None:
        self._queue = queue
        self._clock = clock

    def collect(self, raw_events: Sequence[Any], user_id: UUID) -> CollectResult:
        """Проверяет события, обогащает их и отправляет в очередь.

        Raises:
            QueueUnavailableError: очередь не приняла события.
        """
        received_at = self._clock()
        messages: list[QueuedEvent] = []
        rejected: list[RejectedEvent] = []

        for index, raw_event in enumerate(raw_events):
            try:
                event = EVENT_ADAPTER.validate_python(raw_event)
            except ValidationError as error:
                rejected.append(RejectedEvent(index=index, code=INVALID_EVENT, detail=_first_error(error)))
                continue
            messages.append(
                QueuedEvent(
                    # Ключ партиции — сессия просмотра: события одной сессии
                    # приходят потребителю по порядку, а разные сессии
                    # раскладываются по партициям равномерно (ADR-2).
                    key=str(event.session_id),
                    value=_serialize(event, user_id=user_id, received_at=received_at),
                ),
            )

        if messages:
            self._queue.publish(messages)
            logger.info('Принято %d событий пользователя %s', len(messages), user_id)
        if rejected:
            logger.info('Отклонено %d событий пользователя %s', len(rejected), user_id)
        return CollectResult(accepted=len(messages), rejected=rejected)


def _serialize(event, user_id: UUID, received_at: datetime) -> bytes:
    """Собирает сообщение для очереди: событие плюс то, что знает сервис.

    `user_id` берётся из токена, а не из тела запроса, иначе клиент мог бы
    прислать чужой. `received_at` — время приёма: по расхождению с
    `occurred_at` видно, насколько врут часы клиента и как долго событие
    лежало в его очереди.
    """
    payload = event.model_dump(mode='json')
    payload['user_id'] = str(user_id)
    payload['received_at'] = received_at.isoformat()
    return _dumps(payload)


def _dumps(payload: dict) -> bytes:
    # separators без пробелов: на 60 миллионах событий в сутки лишний пробел
    # после каждого двоеточия — это десятки мегабайт трафика в день.
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()


def _first_error(error: ValidationError) -> str:
    """Первая ошибка проверки одной строкой: «поле: что не так».

    Целиком список ошибок pydantic клиенту не отдаётся — в нём повторяется
    присланное значение, а возвращать его в ответе нельзя: в журналах и
    прокси окажется то, что клиент прислал.
    """
    first = error.errors()[0]
    location = '.'.join(str(part) for part in first['loc']) or 'event'
    return f'{location}: {first["msg"]}'
