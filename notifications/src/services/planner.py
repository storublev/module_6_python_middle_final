"""Планировщик: кому и когда.

Единственное место, где решается «кому писать». Он разворачивает адресацию в
список получателей, отсеивает отписавшихся и тех, кому об этой версии данных
уже сообщали, режет остаток на пачки и отправляет их сборщику.

Почему разворачивание живёт здесь, а не у отправителя события. Событие
описывает факт: «вышла восьмая серия сериала N». Кто на этот сериал подписан —
знание сервиса уведомлений, и тащить его в каталог или в UGC значит размазать
одну ответственность по трём сервисам.

Почему пачками. Сборщик берёт контакты одним запросом на пачку, а не по
одному на человека: тысяча запросов в сервис авторизации на рассылку — это
ровно тот отказ подсистемы данных, от которого предостерегает задача урока
про RabbitMQ.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

from core.request_id import get_request_id
from models.enums import AudienceKind
from services.messages import PlanMessage, RenderMessage
from storage.base import (
    ContactDirectory,
    MessagePublisher,
    NotificationRepository,
    SubscriptionRepository,
    TemplateRepository,
)
from storage.rabbit import STAGE_RENDER

logger = logging.getLogger(__name__)


class PlannerService:
    """Разворачивает событие в пачки получателей."""

    def __init__(
        self,
        templates: TemplateRepository,
        subscriptions: SubscriptionRepository,
        notifications: NotificationRepository,
        directory: ContactDirectory,
        publisher: MessagePublisher,
        batch_size: int,
    ) -> None:
        self._templates = templates
        self._subscriptions = subscriptions
        self._notifications = notifications
        self._directory = directory
        self._publisher = publisher
        self._batch_size = batch_size

    async def plan(self, message: PlanMessage) -> int:
        """Разворачивает событие и отправляет пачки сборщику. Возвращает число адресатов."""
        template = await self._templates.get(message.template_code)
        if template is None or not template.is_active:
            # Шаблона нет — письмо собрать не из чего. Повторять бессмысленно,
            # поэтому не поднимаем ошибку: сообщение подтверждается, а факт
            # уходит в журнал и в мониторинг.
            logger.error(
                'Шаблон не найден или выключен, событие пропущено',
                extra={'template': message.template_code, 'event_id': str(message.event_id)},
            )
            return 0

        planned = 0
        async for batch in self._recipients(message):
            allowed = await self._allowed(message, batch)
            if not allowed:
                continue
            payload = RenderMessage(
                event_id=message.event_id,
                template_code=template.code,
                # Версия фиксируется здесь: правка шаблона посреди рассылки не
                # должна разослать половине зрителей одно письмо, а половине
                # другое.
                template_version=template.version,
                channel=message.channel,
                user_ids=list(allowed),
                context=message.context,
                content_id=message.content_id,
                content_version=message.content_version,
                dataset_key=message.dataset_key,
            )
            await self._publisher.publish(STAGE_RENDER, payload.model_dump(mode='json'), get_request_id())
            planned += len(allowed)
        logger.info(
            'Событие развёрнуто в получателей',
            extra={'event_id': str(message.event_id), 'planned': planned},
        )
        return planned

    async def _recipients(self, message: PlanMessage):  # noqa: ANN202 - асинхронный генератор пачек
        """Отдаёт получателей пачками заданного размера."""
        if message.audience.kind is AudienceKind.USERS:
            for batch in _chunks(message.audience.user_ids, self._batch_size):
                yield batch
            return
        if message.audience.kind is AudienceKind.ALL:
            # Обход всех зрителей — постранично по ключу: миллионы адресатов в
            # памяти планировщика держать негде, да и незачем.
            after: UUID | None = None
            while True:
                page, after = await self._directory.page(after, self._batch_size)
                if not page:
                    return
                yield [recipient.user_id for recipient in page]
                if after is None:
                    return
            return
        # Сегмент: пока поддержан явный список в правиле. Настоящие сегменты
        # («кто смотрел детективы») строит аналитика и передаёт их витриной —
        # здесь остаётся место для этого расширения.
        explicit = message.audience.segment.get('user_ids', [])
        for batch in _chunks([UUID(str(user_id)) for user_id in explicit], self._batch_size):
            yield batch

    async def _allowed(self, message: PlanMessage, batch: Sequence[UUID]) -> list[UUID]:
        """Оставляет тех, кому это письмо действительно нужно."""
        subscribed = await self._subscriptions.filter_enabled(batch, message.template_code, message.channel)
        if not subscribed:
            return []
        if message.content_id is None:
            return [user_id for user_id in batch if user_id in subscribed]
        # О той же версии данных второй раз не пишем: вышла восьмая серия при
        # записанной восьмой — письма не будет (ФТ-6).
        fresh = await self._notifications.filter_outdated(
            sorted(subscribed), message.template_code, message.content_id, message.content_version,
        )
        return [user_id for user_id in batch if user_id in fresh]


def _chunks(items: Sequence[UUID], size: int) -> list[list[UUID]]:
    return [list(items[start:start + size]) for start in range(0, len(items), size)]
