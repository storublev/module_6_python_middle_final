"""Приём событий.

Задача этого слоя ровно одна: **быстро принять и поставить в очередь**. Ничего
не собирать, никуда не ходить, никого не рассылать. Так требует и урок
(«сам API не занимается рассылкой — это центральный узел»), и здравый смысл:
источник события ждёт ответа, и если API начнёт собирать данные на тысячи
адресатов, http-запрос админа «опубликовать серию» повиснет на минуты.

Идемпотентность приёма — на `event_id`, который задаёт отправитель. Повтор
запроса после потерянного ответа не должен рождать второе уведомление (ФТ-2),
поэтому решение принимает уникальный ключ в базе, а не проверка «нет ли
такого» перед вставкой.

В RabbitMQ API напрямую не пишет. Событие и задание на публикацию ложатся в
базу одной транзакцией (outbox), а в брокер их переносит ретранслятор
(`services/relay.py`). Раньше событие записывалось, а публикация шла следом:
лежащий брокер оставлял событие в базе без сообщения в очереди, и повтор
запроса отвечал «уже принято» — письмо терялось насовсем.
"""

import logging

from core.request_id import get_request_id
from models.event import AcceptedEvent, Event
from models.outbox import OutboxDraft
from services.messages import PlanMessage
from storage.base import EventStore
from storage.rabbit import STAGE_PLAN

logger = logging.getLogger(__name__)


class IngestService:
    """Приём событий и заявок на рассылку."""

    def __init__(self, events: EventStore) -> None:
        self._events = events

    async def accept(self, event: Event) -> AcceptedEvent:
        """Принимает событие: запоминает его вместе с заданием на публикацию."""
        message = PlanMessage(
            event_id=event.event_id,
            routing_key=event.routing_key,
            template_code=event.template_code,
            channel=event.channel,
            urgency=event.urgency,
            audience=event.audience,
            content_id=event.content_id,
            content_version=event.content_version,
            context=event.context,
            dataset_key=event.dataset_key,
        )
        publication = OutboxDraft(
            stage=STAGE_PLAN, payload=message.model_dump(mode='json'), request_id=get_request_id(),
        )
        if not await self._events.remember(event, publication):
            logger.info(
                'Событие уже принимали, повтор не создаёт уведомление',
                extra={'event_id': str(event.event_id), 'routing_key': event.routing_key},
            )
            return AcceptedEvent(event_id=event.event_id, accepted=False)
        logger.info(
            'Событие принято и поставлено в очередь',
            extra={'event_id': str(event.event_id), 'routing_key': event.routing_key},
        )
        return AcceptedEvent(event_id=event.event_id, accepted=True)
