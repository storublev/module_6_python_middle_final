"""Приём событий.

Задача этого слоя ровно одна: **быстро принять и положить в очередь**. Ничего
не собирать, никуда не ходить, никого не рассылать. Так требует и урок
(«сам API не занимается рассылкой — это центральный узел»), и здравый смысл:
источник события ждёт ответа, и если API начнёт собирать данные на тысячи
адресатов, http-запрос админа «опубликовать серию» повиснет на минуты.

Идемпотентность приёма — на `event_id`, который задаёт отправитель. Повтор
запроса после потерянного ответа не должен рождать второе уведомление (ФТ-2),
поэтому решение принимает уникальный ключ в базе, а не проверка «нет ли
такого» перед вставкой.
"""

import logging

from core.request_id import get_request_id
from models.event import AcceptedEvent, Event
from services.messages import PlanMessage
from storage.base import EventStore, MessagePublisher
from storage.rabbit import STAGE_PLAN

logger = logging.getLogger(__name__)


class IngestService:
    """Приём событий и заявок на рассылку."""

    def __init__(self, events: EventStore, publisher: MessagePublisher) -> None:
        self._events = events
        self._publisher = publisher

    async def accept(self, event: Event) -> AcceptedEvent:
        """Принимает событие: запоминает и кладёт в очередь.

        Порядок важен: сначала запись в базу, потом публикация. Если поменять
        местами, падение между шагами оставит сообщение в очереди без записи —
        и повтор запроса создаст второе.
        """
        fresh = await self._events.remember(event)
        if not fresh:
            logger.info(
                'Событие уже принимали, повтор не создаёт уведомление',
                extra={'event_id': str(event.event_id), 'routing_key': event.routing_key},
            )
            return AcceptedEvent(event_id=event.event_id, accepted=False)

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
        await self._publisher.publish(STAGE_PLAN, message.model_dump(mode='json'), get_request_id())
        logger.info(
            'Событие принято и отправлено в очередь',
            extra={'event_id': str(event.event_id), 'routing_key': event.routing_key},
        )
        return AcceptedEvent(event_id=event.event_id, accepted=True)
