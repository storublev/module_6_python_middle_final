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

Прямо перед отправкой — ещё две проверки, которые раньше делались только в
начале конвейера. Между выбором получателей и отправкой письмо может долго
лежать в очереди: за это время зритель успевает отписаться, а у него
наступает ночь. Поэтому:

* **подписка** проверяется снова: отписался — письмо отменяется (`SKIPPED`) и
  не уходит, даже если подпишется обратно — это письмо он уже отверг;
* **тихие часы** проверяются снова: ночь — письмо откладывается до утра в
  outbox и вернётся в очередь отправки в 9:00 по времени зрителя.

Временная ошибка канала снимает аренду и поднимается наружу: воркер отправит
сообщение в отложенный повтор, и письмо всё-таки уйдёт. Постоянный отказ
(нет такого ящика) повторять бессмысленно — он записывается как неудача.
"""

import logging
from datetime import datetime, timedelta, timezone

from channels.base import ChannelUnavailableError, DeliveryChannel, MessageRejectedError
from core.request_id import get_request_id
from models.enums import ClaimState, DeliveryStatus
from models.notification import RenderedMessage
from models.outbox import OutboxDraft
from services.messages import SendMessage
from services.quiet_hours import QuietHours
from storage.base import DeliveryRepository, NotificationRepository, Outbox, SubscriptionRepository
from storage.rabbit import STAGE_SEND

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
        subscriptions: SubscriptionRepository,
        outbox: Outbox,
        quiet_hours: QuietHours,
    ) -> None:
        self._deliveries = deliveries
        self._notifications = notifications
        self._channels = channels
        self._lease = lease
        self._subscriptions = subscriptions
        self._outbox = outbox
        self._quiet = quiet_hours

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

        allowed = await self._subscriptions.filter_enabled(
            [message.user_id], message.template_code, message.channel,
        )
        if message.user_id not in allowed:
            # Отписка между планированием и отправкой: письмо отменяется
            # окончательно, а в истории видно, почему оно не пришло.
            await self._deliveries.finish(
                message.idempotency_key, DeliveryStatus.SKIPPED, 'Зритель отписался до отправки',
            )
            logger.info('Зритель отписался, пока письмо ждало отправки: письмо отменено', extra=log_extra)
            return False
        if self._quiet.is_quiet(moment, message.timezone):
            await self._postpone(message, moment)
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

    async def _postpone(self, message: SendMessage, moment: datetime) -> None:
        """Откладывает готовое письмо до утра зрителя.

        Письмо ждёт в outbox и вернётся в очередь отправки, когда наступит
        утро. Аренда снимается: утром письмо заберёт тот отправитель, к
        которому оно попадёт.
        """
        morning = self._quiet.next_open(moment, message.timezone)
        await self._outbox.put(OutboxDraft(
            stage=STAGE_SEND,
            payload=message.model_dump(mode='json'),
            request_id=get_request_id(),
            available_at=morning,
        ))
        await self._deliveries.release(message.idempotency_key, f'Отложено до {morning.isoformat()}: ночь у зрителя')
        logger.info(
            'У зрителя наступила ночь, пока письмо ждало отправки: отложено до утра',
            extra={'user_id': str(message.user_id), 'template': message.template_code, 'until': morning.isoformat()},
        )
