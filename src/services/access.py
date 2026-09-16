"""Что из каталога открыто пользователю.

ETL помечает каждый фильм уровнем доступа: `subscription` — вышедшим менее
трёх лет назад, `public` — остальным. Правило «три года» живёт в ETL, сервис
контента про него не знает и работает только с меткой.

Кто есть кто, знает сервис авторизации. Права спрашиваются у него, а не
разбираются из токена на месте: наш access-токен действует, пока жива сессия,
поэтому подпись токена ничего не говорит о том, не отозвана ли подписка и не
закрыта ли сессия. Цена — один запрос к сервису авторизации на запрос
каталога, и только когда клиент прислал токен: анонимному и так доступно
только публичное.

Если сервис авторизации недоступен, каталог не отказывает: выдача
деградирует до публичного контента (`degraded`), а не отвечает ошибкой. Это
и есть изящная деградация — самый нагруженный сервис сайта не должен ронять
вместе с собой выдачу фильмов.
"""

import logging
from dataclasses import dataclass

from models.film import AccessLevel
from storage.access import AccessGateway, AccessUnavailableError

logger = logging.getLogger(__name__)

# Право смотреть фильмы по подписке; его выдаёт роль subscribers.
FILMS_SUBSCRIPTION = 'films.subscription'


@dataclass(frozen=True)
class Access:
    """Уровни доступа, открытые пользователю этого запроса."""

    levels: tuple[str, ...]
    # Права не проверены: сервис авторизации не ответил. Выдача урезана до
    # публичной, поэтому отказ в подписочном фильме — временный, а не окончательный.
    degraded: bool = False

    def allows(self, level: str) -> bool:
        return level in self.levels


# Доступное анонимному пользователю — и всем остальным, когда прав не видно.
PUBLIC_ONLY = Access((AccessLevel.PUBLIC,))


class AccessService:
    """Определяет по токену, какие уровни доступа открыты пользователю."""

    def __init__(self, gateway: AccessGateway):
        self.gateway = gateway

    async def for_token(self, token: str | None) -> Access:
        """Уровни доступа владельца токена.

        Raises:
            TokenRejectedError: сервис авторизации не принял токен.
        """
        if token is None:
            # Анонимный запрос: прав нет и спрашивать не о чем — сервис
            # авторизации не тревожим.
            return PUBLIC_ONLY
        try:
            allowed = await self.gateway.check(token, FILMS_SUBSCRIPTION)
        except AccessUnavailableError as exc:
            logger.warning('Права не проверены, выдаём только публичные фильмы: %s', exc)
            return Access((AccessLevel.PUBLIC,), degraded=True)
        return Access((AccessLevel.PUBLIC, AccessLevel.SUBSCRIPTION)) if allowed else PUBLIC_ONLY
