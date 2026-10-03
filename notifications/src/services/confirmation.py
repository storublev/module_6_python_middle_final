"""Подтверждение адреса почты по ссылке из письма.

Урок «Короткие ссылки» описывает ссылку подтверждения: в ней идентификатор
зрителя, срок действия и `redirectUrl`. Одного идентификатора мало — он не
секрет, его видно в токене доступа, в журналах, в соседних сервисах. Если
подтверждение ставится по нему, адрес может «подтвердить» любой, кто этот
идентификатор узнал, не заглядывая в ящик.

Поэтому ссылку несёт **одноразовый токен**:

* случайный, 32 байта из `secrets` — его не подобрать и не вычислить;
* привязан к зрителю и адресу, на который ушло письмо: подтверждается ровно
  этот ящик, и смена почты старое подтверждение не переносит;
* со сроком действия — тем же, что у короткой ссылки;
* гасится при первом переходе;
* в базе лежит его SHA-256, а не он сам.

Подтверждение хранится отдельно от подписок и общий отказ от рассылок не
трогает: владение ящиком — не согласие на письма.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

from models.notification import EmailConfirmation
from services.errors import ConfirmationLinkInvalidError
from services.shortlinks import CONFIRM_PURPOSE, ShortLinkService
from storage.base import EmailConfirmationRepository

logger = logging.getLogger(__name__)

# 32 байта случайности — столько же, сколько у ключа подписи токенов доступа.
TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    """Хеш токена для хранения.

    SHA-256 без соли достаточно: токен — 256 бит случайности, а не пароль,
    который подбирают по словарю.
    """
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class EmailConfirmationService:
    """Выдача и погашение ссылок подтверждения почты."""

    def __init__(
        self,
        confirmations: EmailConfirmationRepository,
        links: ShortLinkService,
        base_url: str,
        ttl: timedelta,
    ) -> None:
        self._confirmations = confirmations
        self._links = links
        self._base_url = base_url.rstrip('/')
        self._ttl = ttl

    async def link_for(self, user_id: UUID, email: str, redirect_url: str, now: datetime | None = None) -> str:
        """Короткая ссылка подтверждения для письма.

        Токен и короткая ссылка живут одинаково долго: переход по ещё живой
        короткой ссылке не должен упираться в истёкший токен.
        """
        moment = now or datetime.now(timezone.utc)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        await self._confirmations.issue(hash_token(token), user_id, email, moment + self._ttl)
        params = urlencode({'token': token, 'redirectUrl': redirect_url})
        target = f'{self._base_url}/notify/api/v1/confirm-email?{params}'
        link = await self._links.shorten(target, user_id=user_id, ttl=self._ttl, purpose=CONFIRM_PURPOSE)
        return self._links.url_of(link.key)

    async def confirm(self, token: str, now: datetime | None = None) -> EmailConfirmation:
        """Подтверждает адрес по токену из ссылки.

        Raises:
            ConfirmationLinkInvalidError: токена нет, срок вышел или его уже использовали.
        """
        confirmation = await self._confirmations.confirm(hash_token(token), now or datetime.now(timezone.utc))
        if confirmation is None:
            raise ConfirmationLinkInvalidError
        logger.info('Адрес почты подтверждён', extra={'user_id': str(confirmation.user_id)})
        return confirmation

    async def status(self, user_id: UUID) -> EmailConfirmation | None:
        """Подтверждённый адрес зрителя или None."""
        return await self._confirmations.get(user_id)
