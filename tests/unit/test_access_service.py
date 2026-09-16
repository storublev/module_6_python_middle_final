"""AccessService: что открыто пользователю и что происходит без сервиса авторизации."""

import pytest

from models.film import AccessLevel
from services.access import FILMS_SUBSCRIPTION, AccessService
from storage.access import AccessGateway, AccessUnavailableError, TokenRejectedError

TOKEN = 'a.b.c'


class FakeGateway(AccessGateway):
    """Сервис авторизации, отвечающий заданным вердиктом или ошибкой."""

    def __init__(self, allowed: bool = False, error: Exception | None = None) -> None:
        self.allowed = allowed
        self.error = error
        self.asked: list[tuple[str, str]] = []

    async def check(self, token: str, permission: str) -> bool:
        self.asked.append((token, permission))
        if self.error:
            raise self.error
        return self.allowed


async def test_anonymous_sees_public_films_only():
    """Без токена доступны только публичные фильмы."""
    access = await AccessService(FakeGateway()).for_token(None)

    assert access.levels == (AccessLevel.PUBLIC,)


async def test_anonymous_request_does_not_reach_auth_service():
    """Анонимный запрос не тревожит сервис авторизации: прав у анонима всё равно нет."""
    gateway = FakeGateway()

    await AccessService(gateway).for_token(None)

    assert gateway.asked == []


async def test_subscriber_sees_subscription_films():
    """С правом films.subscription открываются и подписочные фильмы."""
    access = await AccessService(FakeGateway(allowed=True)).for_token(TOKEN)

    assert access.allows(AccessLevel.SUBSCRIPTION)


async def test_subscription_permission_is_asked_for():
    """Спрашивается именно право на подписочные фильмы."""
    gateway = FakeGateway(allowed=True)

    await AccessService(gateway).for_token(TOKEN)

    assert gateway.asked == [(TOKEN, FILMS_SUBSCRIPTION)]


async def test_user_without_subscription_sees_public_films_only():
    """Пользователь без подписки видит то же, что аноним."""
    access = await AccessService(FakeGateway(allowed=False)).for_token(TOKEN)

    assert access.levels == (AccessLevel.PUBLIC,)
    assert not access.degraded


async def test_unavailable_auth_service_degrades_to_public():
    """Сервис авторизации молчит — каталог не падает, а сужается до публичных фильмов."""
    gateway = FakeGateway(error=AccessUnavailableError('circuit breaker is open'))

    access = await AccessService(gateway).for_token(TOKEN)

    assert access.levels == (AccessLevel.PUBLIC,)


async def test_degradation_is_marked():
    """Урезанная выдача помечается: отказ в подписочном фильме тогда временный, а не окончательный."""
    gateway = FakeGateway(error=AccessUnavailableError('timeout'))

    access = await AccessService(gateway).for_token(TOKEN)

    assert access.degraded


async def test_rejected_token_is_not_silently_downgraded():
    """Отказ по токену доходит до клиента: иначе истёкший токен молча работал бы как анонимный."""
    gateway = FakeGateway(error=TokenRejectedError('token_expired', 'Token has expired'))

    with pytest.raises(TokenRejectedError):
        await AccessService(gateway).for_token(TOKEN)
