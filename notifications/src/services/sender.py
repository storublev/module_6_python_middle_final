"""Отправитель: последний шаг, после которого письмо уже не вернуть.

Порядок шагов здесь — главное в защите от дублей (ADR-11):

1. **занять ключ идемпотентности** в базе. Если он занят, письмо уже
   отправлено или отправляется прямо сейчас — выходим, ничего не делая. Это и
   есть страховка поверх гарантии at-least-once: брокер вправе доставить
   сообщение повторно, и это нормально;
2. отправить через канал;
3. отметить итог.

Почему бронь до отправки, а не после. Падение между отправкой и записью в
худшем случае даёт письмо, помеченное отправленным, но не ушедшее: это видно
в истории и чинится повтором. Обратный порядок дал бы дубль — а дубль
пользователю виден и обиден, о чём прямо говорит урок.

Временная ошибка канала снимает бронь и поднимается наружу: воркер отправит
сообщение в отложенный повтор, и письмо всё-таки уйдёт. Постоянный отказ
(нет такого ящика) повторять бессмысленно — он записывается как неудача.
"""

import logging
from datetime import datetime, timezone

from channels.base import ChannelUnavailableError, DeliveryChannel, MessageRejectedError
from models.enums import DeliveryStatus
from models.notification import RenderedMessage
from services.messages import SendMessage
from storage.base import DeliveryRepository, NotificationRepository

logger = logging.getLogger(__name__)


class SenderService:
    """Отправка одного готового сообщения."""

    def __init__(
        self,
        deliveries: DeliveryRepository,
        notifications: NotificationRepository,
        channels: dict[str, DeliveryChannel],
    ) -> None:
        self._deliveries = deliveries
        self._notifications = notifications
        self._channels = channels

    async def send(self, message: SendMessage, now: datetime | None = None) -> bool:
        """Отправляет сообщение. Возвращает False, если оно уже было отправлено.

        Raises:
            ChannelUnavailableError: канал временно не работает — повторить позже.
        """
        moment = now or datetime.now(timezone.utc)
        rendered = RenderedMessage(
            idempotency_key=message.idempotency_key,
            user_id=message.user_id,
            channel=message.channel,
            template_code=message.template_code,
            address=message.address,
            subject=message.subject,
            body=message.body,
        )
        reserved = await self._deliveries.reserve(rendered)
        if reserved is None:
            logger.info(
                'Письмо уже отправлено, повтор пропущен',
                extra={'user_id': str(message.user_id), 'template': message.template_code},
            )
            return False

        channel = self._channels.get(message.channel.value)
        if channel is None:
            await self._deliveries.finish(
                message.idempotency_key, DeliveryStatus.FAILED, f'Канал {message.channel.value} не подключён',
            )
            return False

        try:
            await channel.send(rendered)
        except MessageRejectedError as error:
            # Навсегда: адрес не станет существующим от ожидания. Бронь не
            # снимаем — повтор ничего не изменит, а запись нужна для разбора.
            await self._deliveries.finish(message.idempotency_key, DeliveryStatus.FAILED, str(error))
            logger.warning(
                'Канал отказался от сообщения навсегда: %s', error,
                extra={'user_id': str(message.user_id)},
            )
            return False
        except ChannelUnavailableError:
            # Временно: снимаем бронь, иначе повтор упрётся в собственный ключ
            # и письмо не уйдёт никогда.
            await self._deliveries.release(message.idempotency_key)
            raise

        await self._deliveries.finish(message.idempotency_key, DeliveryStatus.SENT)
        if message.content_id is not None:
            # Запоминаем версию данных, о которой сообщили: следующее событие
            # с той же версией письма уже не породит.
            await self._notifications.mark_notified(
                message.user_id, message.template_code, message.content_id, message.content_version, moment,
            )
        logger.info(
            'Сообщение отправлено',
            extra={'user_id': str(message.user_id), 'template': message.template_code,
                   'channel': message.channel.value},
        )
        return True
