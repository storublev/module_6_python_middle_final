"""Сокращение ссылок.

Чек-лист задания требует отдельный сервис сокращения ссылок: «в наших
сообщениях будет много ссылок, и все они будут удобнее в сокращённом виде».
Урок «Короткие ссылки» задаёт и остальные требования: ссылка подтверждения
почты несёт идентификатор зрителя (чтобы считать визиты), срок действия и
адрес, куда вести после подтверждения; просроченная отдаёт 404.

Переход отвечает **302 Found**, а не 301: 301 браузер кеширует навсегда, и
после срока действия он всё равно поведёт по старому адресу, не спросив нас.
Для ссылки со сроком жизни это неверно.
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

from models.notification import ShortLink
from services.errors import LinkNotFoundError
from storage.base import ShortLinkRepository

logger = logging.getLogger(__name__)

CONFIRM_PURPOSE = 'confirm-email'


class ShortLinkService:
    """Выдача и разбор коротких ссылок."""

    def __init__(self, links: ShortLinkRepository, base_url: str, confirm_ttl: timedelta) -> None:
        self._links = links
        self._base_url = base_url.rstrip('/')
        self._confirm_ttl = confirm_ttl

    async def shorten(
        self,
        target_url: str,
        user_id: UUID | None = None,
        ttl: timedelta | None = None,
        purpose: str | None = None,
    ) -> ShortLink:
        """Заводит короткую ссылку."""
        expires_at = datetime.now(timezone.utc) + ttl if ttl else None
        return await self._links.create(target_url, user_id, expires_at, purpose)

    async def confirmation_link(self, user_id: UUID, redirect_url: str) -> str:
        """Ссылка подтверждения адреса почты из приветственного письма.

        Несёт идентификатор зрителя, срок действия и `redirectUrl` — ровно то,
        что перечисляет задание урока.
        """
        params = urlencode({'user_id': str(user_id), 'redirectUrl': redirect_url})
        target = f'{self._base_url}/notify/api/v1/confirm-email?{params}'
        link = await self.shorten(target, user_id=user_id, ttl=self._confirm_ttl, purpose=CONFIRM_PURPOSE)
        return self.url_of(link.key)

    async def resolve(self, key: str, now: datetime | None = None) -> ShortLink:
        """Отдаёт ссылку по ключу и считает переход.

        Raises:
            LinkNotFoundError: ключа нет или срок вышел — по заданию это 404.
        """
        link = await self._links.resolve(key, now or datetime.now(timezone.utc))
        if link is None:
            raise LinkNotFoundError
        return link

    def url_of(self, key: str) -> str:
        return f'{self._base_url}/s/{key}'
