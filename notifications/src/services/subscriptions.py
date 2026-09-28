"""Настройки уведомлений зрителя.

Задание требует, чтобы зритель мог настраивать уведомления и отключать их.
Устроено так: **отсутствие записи означает согласие**, а отказ хранится явно.
Иначе при добавлении нового типа уведомлений пришлось бы завести согласие
миллиону зрителей, а до тех пор не писать никому.

Ссылка отписки в письме работает **без входа в аккаунт** (ФТ-11): человек,
который хочет отписаться, не должен вспоминать пароль. Ссылка одноразовая по
смыслу, а её подлинность подтверждает подпись — отписать чужого по угаданной
ссылке нельзя.
"""

import hashlib
import hmac
import logging
from uuid import UUID

from models.enums import Channel
from models.notification import Subscription
from services.errors import TokenInvalidError
from storage.base import SubscriptionRepository

logger = logging.getLogger(__name__)

UNSUBSCRIBE_PURPOSE = 'unsubscribe'
# Служебный код подписки, которым отмечается подтверждённый адрес.
EMAIL_CONFIRMED_CODE = 'email_confirmed'


class SubscriptionService:
    """Подписки зрителя и отписка по ссылке из письма."""

    def __init__(self, subscriptions: SubscriptionRepository, secret: str) -> None:
        self._subscriptions = subscriptions
        self._secret = secret

    async def list_for_user(self, user_id: UUID) -> list[Subscription]:
        return await self._subscriptions.list_for_user(user_id)

    async def set_enabled(
        self, user_id: UUID, template_code: str, channel: Channel, enabled: bool,
    ) -> Subscription:
        logger.info(
            'Настройка уведомлений изменена',
            extra={'user_id': str(user_id), 'template': template_code, 'enabled': enabled},
        )
        return await self._subscriptions.set_enabled(user_id, template_code, channel, enabled)

    async def unsubscribe_all(self, user_id: UUID) -> None:
        await self._subscriptions.unsubscribe_all(user_id)

    async def confirm_email(self, user_id: UUID) -> None:
        """Отмечает адрес подтверждённым.

        Переход по короткой ссылке с неугадываемым ключом и есть
        доказательство, что письмо дошло до владельца ящика. Отметка ставится
        явной подпиской на почтовый канал: подтверждённый адрес — это адрес, на
        который можно писать.
        """
        await self._subscriptions.set_enabled(user_id, EMAIL_CONFIRMED_CODE, Channel.EMAIL, True)
        logger.info('Адрес почты подтверждён', extra={'user_id': str(user_id)})

    def unsubscribe_token(self, user_id: UUID) -> str:
        """Подпись для ссылки отписки.

        HMAC от идентификатора зрителя тем же секретом, которым подписываются
        токены: подобрать её нельзя, а проверять её можно, ничего не храня.
        """
        return hmac.new(self._secret.encode('utf-8'), str(user_id).encode('utf-8'), hashlib.sha256).hexdigest()

    async def unsubscribe_by_token(self, user_id: UUID, token: str) -> None:
        """Отписывает от всего по ссылке из письма.

        Raises:
            TokenInvalidError: подпись не совпала.
        """
        # Сравнение постоянного времени: обычное `==` завершается на первом
        # различии, и по времени ответа подпись подбирается посимвольно.
        #
        # Сравниваются байты, а не строки: `compare_digest` на строках с
        # не-ASCII символами поднимает TypeError, и подпись из кириллицы
        # роняла бы эндпоинт пятисоткой вместо честного отказа. Нашёл
        # функциональный тест.
        expected = self.unsubscribe_token(user_id).encode('utf-8')
        if not hmac.compare_digest(token.encode('utf-8'), expected):
            raise TokenInvalidError('Unsubscribe link is not valid')
        await self._subscriptions.unsubscribe_all(user_id)
        logger.info('Зритель отписался от всех уведомлений', extra={'user_id': str(user_id)})
