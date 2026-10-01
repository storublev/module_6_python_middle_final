"""Сборщик: превращает пачку получателей в готовые письма.

Здесь происходит вторая половина гибридной схемы: **данные получателя
добираются пачкой**. На тысячу адресатов — один запрос в справочник контактов
сервиса авторизации, а не тысяча.

Здесь же — два решения, которые принимаются только в последний момент:

* **окно суток.** Письмо, собранное ночью по времени зрителя, не уходит: оно
  вернётся в очередь и подождёт утра. Ночная отправка — самая обидная ошибка
  рассылки, о ней прямо предупреждает урок «Как испортить жизнь клиенту»;
* **актуальность.** Между событием и сборкой могло пройти время, и событие
  успело протухнуть: зритель уже посмотрел ту самую серию. Право не отправлять
  урок отдаёт именно воркеру, и оно реализуется здесь.
"""

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.request_id import get_request_id
from models.notification import Recipient, Template
from services.confirmation import EmailConfirmationService
from services.errors import TemplateInvalidError
from services.messages import RenderMessage, SendMessage
from services.renderer import Renderer
from services.subscriptions import unsubscribe_url
from storage.base import ContactDirectory, MessagePublisher, TemplateRepository
from storage.rabbit import STAGE_RENDER, STAGE_SEND

logger = logging.getLogger(__name__)

# Переменная шаблона со ссылкой подтверждения почты.
CONFIRM_VARIABLE = 'confirm_url'


class QuietHours:
    """Окно суток, в которое зрителю писать нельзя.

    Считается в его собственном часовом поясе: у кинотеатра зрители от
    Калининграда до Владивостока, и одно московское время для всех означает
    письмо в три ночи половине страны.
    """

    def __init__(self, start_hour: int, end_hour: int, default_timezone: str) -> None:
        self._start = time(hour=start_hour)
        self._end = time(hour=end_hour)
        self._default = default_timezone

    def zone_of(self, recipient: Recipient) -> ZoneInfo:
        name = recipient.timezone or self._default
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            # Часовой пояс проверяется при сохранении профиля, но данные
            # приходят из чужого сервиса: испорченное значение не должно
            # ронять рассылку.
            logger.warning('Неизвестный часовой пояс %s, взят %s', name, self._default)
            return ZoneInfo(self._default)

    def is_quiet(self, moment: datetime, recipient: Recipient) -> bool:
        """Тихое ли сейчас время у получателя."""
        local = moment.astimezone(self.zone_of(recipient)).time()
        if self._start <= self._end:
            return self._start <= local < self._end
        # Окно через полночь (21:00–09:00) — самый обычный случай.
        return local >= self._start or local < self._end

    def next_open(self, moment: datetime, recipient: Recipient) -> datetime:
        """Ближайший момент, когда писать снова можно."""
        zone = self.zone_of(recipient)
        local = moment.astimezone(zone)
        opening = local.replace(hour=self._end.hour, minute=0, second=0, microsecond=0)
        if opening <= local:
            opening += timedelta(days=1)
        return opening.astimezone(timezone.utc)


class AssemblyService:
    """Сборка писем для пачки получателей."""

    def __init__(
        self,
        templates: TemplateRepository,
        directory: ContactDirectory,
        renderer: Renderer,
        publisher: MessagePublisher,
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
            await self._defer(message, deferred)

        # Ссылка подтверждения — только если шаблон её выводит: токен на
        # каждое письмо подборки засорял бы базу ссылками, по которым никто не
        # перейдёт.
        wants_confirmation = self._confirmations is not None and self._renderer.uses(template, CONFIRM_VARIABLE)
        built = 0
        for recipient in ready:
            extra = await self._confirmation_context(recipient) if wants_confirmation else {}
            send = self._build(template, recipient, message, extra)
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
            (deferred if self._quiet.is_quiet(moment, recipient) else ready).append(recipient)
        return ready, deferred

    async def _defer(self, message: RenderMessage, recipients: Sequence[Recipient]) -> None:
        """Возвращает ночных получателей в очередь сборки — они дождутся утра."""
        payload = message.model_copy(update={'user_ids': [recipient.user_id for recipient in recipients]})
        await self._publisher.publish(STAGE_RENDER, payload.model_dump(mode='json'), get_request_id())
        logger.info(
            'Ночным получателям письмо отложено до утра',
            extra={'event_id': str(message.event_id), 'deferred': len(recipients)},
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

    def _build(
        self, template: Template, recipient: Recipient, message: RenderMessage, extra: dict[str, Any],
    ) -> SendMessage | None:
        context = {
            **message.context,
            **extra,
            'site_url': self._base_url,
            # Ссылка отписки своя у каждого получателя: в ней идентификатор и
            # подпись. Общая ссылка без них не работает — переход из письма
            # упирается в проверку параметров.
            'unsubscribe_url': unsubscribe_url(self._base_url, recipient.user_id, self._secret),
        }
        try:
            subject, body = self._renderer.render(template, recipient, context)
        except TemplateInvalidError as error:
            # Шаблон проверяется при сохранении, но данные конкретного письма
            # могли оказаться неожиданными. Одно испорченное письмо не должно
            # ронять всю пачку.
            logger.error(
                'Письмо не собралось, получатель пропущен: %s', error,
                extra={'user_id': str(recipient.user_id), 'template': template.code},
            )
            return None
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
        )


def idempotency_key(message: RenderMessage, user_id: UUID) -> str:
    """Ключ, по которому письмо считается уже отправленным.

    Из события, получателя и канала: повторная доставка того же сообщения
    даёт тот же ключ, и второе письмо не уйдёт. Хеш, а не склейка, чтобы
    ключ был фиксированной длины и влезал в индекс.
    """
    raw = f'{message.event_id}|{user_id}|{message.channel.value}|{message.content_version}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
