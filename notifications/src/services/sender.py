"""Отправитель: последний шаг, после которого письмо уже не вернуть.

Порядок шагов здесь — главное в защите от дублей (ADR-11):

1. **забрать письмо под аренду** по ключу идемпотентности. Письмо уже
   отправлено (или отклонено) — выходим, ничего не делая: брокер вправе
   доставить сообщение повторно, и это нормально. Письмо прямо сейчас
   отправляет другой отправитель — сообщение возвращается в отложенный
   повтор, а не подтверждается: если тот отправитель упадёт, письмо должно
   уйти, а не потеряться;
2. отправить через канал;
3. отметить итог.

Почему аренда, а не вечная бронь. Раньше запись `PENDING` ставилась до
отправки, а повтор принимал любую запись за «уже отправлено». Отправитель,
упавший между бронью и отправкой, оставлял письмо неотправленным навсегда.
Аренда даёт срок: вышел — письмо забирает другой отправитель, и ровно один.

Чего аренда не решает и не может. Почтовый сервер мог принять письмо, а
ответ — потеряться (обрыв, таймаут). Узнать, ушло ли письмо, нельзя: SMTP не
даёт ни подтверждения по ключу, ни способа спросить. Выбор — между риском
потерять письмо и риском прислать его дважды. Мы повторяем (at-least-once),
но у каждого письма постоянный `Message-ID` из ключа идемпотентности: повтор
несёт тот же идентификатор, и почтовые службы, которые склеивают письма по
нему (Gmail так делает), покажут одно. Такие повторы отмечаются в журнале
предупреждением — по ним видно, сколько писем могли задвоиться.

Временная ошибка канала снимает аренду и поднимается наружу: воркер отправит
сообщение в отложенный повтор, и письмо всё-таки уйдёт. Постоянный отказ
(нет такого ящика) повторять бессмысленно — он записывается как неудача.
"""

import logging
from datetime import datetime, timedelta, timezone

from channels.base import ChannelUnavailableError, DeliveryChannel, MessageRejectedError
from models.enums import ClaimState, DeliveryStatus
from models.notification import RenderedMessage
from services.messages import SendMessage
from storage.base import DeliveryRepository, NotificationRepository

logger = logging.getLogger(__name__)


class DeliveryInProgressError(Exception):
    """Письмо прямо сейчас отправляет другой отправитель.

    Это не ошибка письма: сообщение надо повторить позже. К тому времени
    письмо будет либо отправлено (повтор выйдет ничего не делая), либо
    брошено (аренда выйдет, и повтор его заберёт).
    """


class SenderService:
    """Отправка одного готового сообщения."""

    def __init__(
        self,
        deliveries: DeliveryRepository,
        notifications: NotificationRepository,
        channels: dict[str, DeliveryChannel],
        lease: timedelta,
    ) -> None:
        self._deliveries = deliveries
        self._notifications = notifications
        self._channels = channels
        self._lease = lease

    async def send(self, message: SendMessage, now: datetime | None = None) -> bool:
        """Отправляет сообщение. Возвращает False, если отправлять не нужно.

        Raises:
            ChannelUnavailableError: канал временно не работает — повторить позже.
            DeliveryInProgressError: письмо отправляет другой отправитель — повторить позже.
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
        claim = await self._deliveries.claim(rendered, self._lease, moment)
        log_extra = {'user_id': str(message.user_id), 'template': message.template_code}
        if claim.state is ClaimState.DONE:
            logger.info('Письмо уже отправлено или отклонено, повтор пропущен', extra=log_extra)
            return False
        if claim.state is ClaimState.BUSY:
            raise DeliveryInProgressError('Письмо отправляет другой отправитель')
        if claim.recovered:
            logger.warning(
                'Письмо забрано у отправителя, пропавшего без итога: возможен дубль, '
                'его склеит постоянный Message-ID',
                extra=log_extra,
            )

        channel = self._channels.get(message.channel.value)
        if channel is None:
            await self._deliveries.finish(
                message.idempotency_key, DeliveryStatus.FAILED, f'Канал {message.channel.value} не подключён',
            )
            return False

        try:
            await channel.send(rendered)
        except MessageRejectedError as error:
            # Навсегда: адрес не станет существующим от ожидания. Запись
            # остаётся окончательной неудачей — повтор ничего не изменит.
            await self._deliveries.finish(message.idempotency_key, DeliveryStatus.FAILED, str(error))
            logger.warning('Канал отказался от сообщения навсегда: %s', error, extra=log_extra)
            return False
        except ChannelUnavailableError as error:
            # Временно: снимаем аренду, чтобы повтор забрал письмо сразу, а не
            # ждал её конца.
            await self._deliveries.release(message.idempotency_key, str(error))
            raise

        await self._deliveries.finish(message.idempotency_key, DeliveryStatus.SENT)
        if message.content_id is not None:
            # Запоминаем версию данных, о которой сообщили: следующее событие
            # с той же версией письма уже не породит.
            await self._notifications.mark_notified(
                message.user_id, message.template_code, message.content_id, message.content_version, moment,
            )
        logger.info('Сообщение отправлено', extra={**log_extra, 'channel': message.channel.value})
        return True
