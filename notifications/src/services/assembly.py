"""Сборщик: превращает пачку получателей в готовые письма.

Здесь происходит вторая половина гибридной схемы: **данные получателя
добираются пачкой**. На тысячу адресатов — один запрос в справочник контактов
сервиса авторизации, а не тысяча.

Здесь же — два решения, которые принимаются только в последний момент:

* **окно суток.** Письмо, собранное ночью по времени зрителя, не уходит: оно
  ждёт утра в базе и возвращается в очередь, когда утро наступит. Ночная
  отправка — самая обидная ошибка рассылки, о ней прямо предупреждает урок
  «Как испортить жизнь клиенту»;
* **актуальность.** Между событием и сборкой могло пройти время, и событие
  успело протухнуть: зритель уже посмотрел ту самую серию. Право не отправлять
  урок отдаёт именно воркеру, и оно реализуется здесь.
"""

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from core.request_id import get_request_id
from models.notification import Recipient, Template
from models.outbox import OutboxDraft
from services.confirmation import EmailConfirmationService
from services.errors import TemplateInvalidError
from services.messages import RenderMessage, SendMessage
from services.quiet_hours import QuietHours
from services.renderer import TemplateEngine, letter_data
from services.subscriptions import unsubscribe_url
from storage.base import ContactDirectory, MessagePublisher, Outbox, TemplateRepository
from storage.rabbit import STAGE_RENDER, STAGE_SEND

logger = logging.getLogger(__name__)

# Переменная шаблона со ссылкой подтверждения почты.
CONFIRM_VARIABLE = 'confirm_url'


class AssemblyService:
    """Сборка писем для пачки получателей."""

    def __init__(
        self,
        templates: TemplateRepository,
        directory: ContactDirectory,
        renderer: TemplateEngine,
        publisher: MessagePublisher,
        outbox: Outbox,
        quiet_hours: QuietHours,
        base_url: str,
        secret: str,
        confirmations: EmailConfirmationService | None = None,
        confirm_redirect_url: str = '',
    ) -> None:
        self._templates = templates
        self._directory = directory
        self._renderer = renderer
        self._publisher = publisher
        self._outbox = outbox
        self._quiet = quiet_hours
        self._base_url = base_url
        # Тот же секрет, которым подписываются токены: им подписывается и
        # ссылка отписки, чтобы её нельзя было подделать.
        self._secret = secret
        self._confirmations = confirmations
        self._confirm_redirect_url = confirm_redirect_url or f'{base_url.rstrip("/")}/'

    async def assemble(self, message: RenderMessage, now: datetime | None = None) -> int:
        """Собирает письма и отправляет их отправителю. Возвращает число собранных."""
        moment = now or datetime.now(timezone.utc)
        template = await self._templates.get_version(message.template_code, message.template_version)
        if template is None:
            logger.error(
                'Версия шаблона не найдена, пачка пропущена',
                extra={'template': message.template_code, 'version': message.template_version},
            )
            return 0

        # Один запрос на всю пачку — ради этого сборщик и отделён от отправителя.
        recipients = await self._directory.contacts(message.user_ids)
        ready, deferred = self._split_by_quiet_hours(recipients, moment)
        if deferred:
            await self._defer(message, deferred, moment)

        # Ссылка подтверждения — только если шаблон её выводит: токен на
        # каждое письмо подборки засорял бы базу ссылками, по которым никто не
        # перейдёт.
        wants_confirmation = self._confirmations is not None and self._renderer.uses(template, CONFIRM_VARIABLE)
        letters = [
            letter_data(recipient, await self._context_for(recipient, message, wants_confirmation))
            for recipient in ready
        ]
        try:
            # Вся пачка собирается одним вызовом: сборка идёт в отдельном
            # процессе, и переход туда раз на пачку дешевле, чем раз на письмо.
            rendered = await self._renderer.render_many(template, letters)
        except TemplateInvalidError as error:
            # Пачка целиком не уложилась в пределы времени или памяти: шаблон
            # тяжёлый для этих данных, и повтор ничего не изменит.
            logger.error(
                'Пачка не собралась и пропущена: %s', error,
                extra={'event_id': str(message.event_id), 'template': template.code},
            )
            return 0
        built = 0
        for recipient, result in zip(ready, rendered, strict=True):
            send = self._message_for(template, recipient, message, result)
            if send is None:
                continue
            await self._publisher.publish(STAGE_SEND, send.model_dump(mode='json'), get_request_id())
            built += 1
        logger.info(
            'Пачка собрана',
            extra={'event_id': str(message.event_id), 'built': built, 'deferred': len(deferred)},
        )
        return built

    def _split_by_quiet_hours(
        self, recipients: Sequence[Recipient], moment: datetime,
    ) -> tuple[list[Recipient], list[Recipient]]:
        ready: list[Recipient] = []
        deferred: list[Recipient] = []
        for recipient in recipients:
            (deferred if self._quiet.is_quiet(moment, recipient.timezone) else ready).append(recipient)
        return ready, deferred

    async def _defer(self, message: RenderMessage, recipients: Sequence[Recipient], moment: datetime) -> None:
        """Откладывает ночных получателей до утра — в базе, а не в очереди.

        Раньше они сразу возвращались в очередь сборки: сборщик тут же забирал
        их снова, опять ходил за контактами, видел ночь и возвращал обратно —
        и так по кругу до утра, нагружая базу и сервис авторизации.

        Теперь время отправки считается заранее (`next_open` в часовом поясе
        зрителя) и записывается вместе с заданием в outbox: ретранслятор
        вернёт его в очередь, только когда это время наступит. Получатели с
        одинаковым утром едут одним заданием — часовых поясов десяток, а не
        тысяча.
        """
        mornings: dict[datetime, list[UUID]] = {}
        for recipient in recipients:
            mornings.setdefault(self._quiet.next_open(moment, recipient.timezone), []).append(recipient.user_id)
        for morning, user_ids in mornings.items():
            payload = message.model_copy(update={'user_ids': user_ids})
            await self._outbox.put(OutboxDraft(
                stage=STAGE_RENDER,
                payload=payload.model_dump(mode='json'),
                request_id=get_request_id(),
                available_at=morning,
            ))
        logger.info(
            'Ночным получателям письмо отложено до утра',
            extra={'event_id': str(message.event_id), 'deferred': len(recipients), 'mornings': len(mornings)},
        )

    async def _confirmation_context(self, recipient: Recipient) -> dict[str, Any]:
        """Персональная ссылка подтверждения почты.

        Токен привязан к адресу, на который уходит письмо: подтвердить можно
        только тот ящик, который его получил.
        """
        if self._confirmations is None or not recipient.email:
            return {}
        url = await self._confirmations.link_for(recipient.user_id, recipient.email, self._confirm_redirect_url)
        return {CONFIRM_VARIABLE: url}

    async def _context_for(
        self, recipient: Recipient, message: RenderMessage, wants_confirmation: bool,
    ) -> dict[str, Any]:
        extra = await self._confirmation_context(recipient) if wants_confirmation else {}
        return {
            **message.context,
            **extra,
            'site_url': self._base_url,
            # Ссылка отписки своя у каждого получателя: в ней идентификатор и
            # подпись. Общая ссылка без них не работает — переход из письма
            # упирается в проверку параметров.
            'unsubscribe_url': unsubscribe_url(self._base_url, recipient.user_id, self._secret),
        }

    def _message_for(
        self,
        template: Template,
        recipient: Recipient,
        message: RenderMessage,
        result: tuple[str, str] | TemplateInvalidError,
    ) -> SendMessage | None:
        if isinstance(result, TemplateInvalidError):
            # Шаблон проверяется при сохранении, но данные конкретного письма
            # могли оказаться неожиданными. Одно испорченное письмо не должно
            # ронять всю пачку.
            logger.error(
                'Письмо не собралось, получатель пропущен: %s', result,
                extra={'user_id': str(recipient.user_id), 'template': template.code},
            )
            return None
        subject, body = result
        address = recipient.email or ''
        return SendMessage(
            idempotency_key=idempotency_key(message, recipient.user_id),
            user_id=recipient.user_id,
            channel=message.channel,
            template_code=template.code,
            address=address,
            subject=subject,
            body=body,
            content_id=message.content_id,
            content_version=message.content_version,
            timezone=recipient.timezone,
        )


def idempotency_key(message: RenderMessage, user_id: UUID) -> str:
    """Ключ, по которому письмо считается уже отправленным.

    Из события, получателя и канала: повторная доставка того же сообщения
    даёт тот же ключ, и второе письмо не уйдёт. Хеш, а не склейка, чтобы
    ключ был фиксированной длины и влезал в индекс.
    """
    raw = f'{message.event_id}|{user_id}|{message.channel.value}|{message.content_version}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
