"""Поставщики OAuth 2.0 — сторона потребителя на Authlib.

Своего сервера OAuth здесь нет: сервис — обычный клиент (consumer), который
ведёт пользователя к поставщику и меняет полученный код на токен. Реализован
Authorization Code Flow: пользователь возвращается с одноразовым кодом, а код
меняется на токен отдельным запросом с бэкенда, где лежит client_secret.
Перехваченный код без секрета бесполезен — этим flow и отличается от
Implicit, отдающего токен прямо в адресной строке.

Поставщики описываются данными (ProviderConfig): адреса, права доступа и
разбор ответа с данными пользователя. Добавить VK или другую соцсеть — значит
дописать сюда описание, а не менять бизнес-логику.

Сбой поставщика выходит наружу как ProviderUnavailableError, отказ в обмене
кода — как ProviderRejectedError: сервис не должен разбираться в исключениях
Authlib и HTTP-клиента.

HTTP-клиент здесь httpx2 — тот, который Authlib использует начиная с 1.8;
старый httpx он считает устаревшим. Сервис контента остался на httpx: образы
у сервисов разные, и общей зависимости между ними нет.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2 as httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client

from models.social import SocialProfile
from storage.base import OAuthProvider, ProviderRejectedError, ProviderUnavailableError

logger = logging.getLogger(__name__)

YANDEX = 'yandex'
GOOGLE = 'google'


def parse_yandex(data: dict[str, Any]) -> SocialProfile:
    """Ответ https://login.yandex.ru/info."""
    return SocialProfile(
        social_id=str(data['id']),
        display_name=data.get('real_name') or data.get('display_name') or data.get('login'),
        email=data.get('default_email'),
    )


def parse_google(data: dict[str, Any]) -> SocialProfile:
    """Ответ https://openidconnect.googleapis.com/v1/userinfo.

    `sub` — постоянный идентификатор пользователя у Google; email может
    смениться, поэтому опознаём по `sub`.
    """
    return SocialProfile(
        social_id=str(data['sub']),
        display_name=data.get('name'),
        email=data.get('email'),
    )


@dataclass(frozen=True)
class ProviderConfig:
    """Всё, что отличает одного поставщика от другого."""

    name: str
    title: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str
    parse: Callable[[dict[str, Any]], SocialProfile]
    # Дополнительные параметры ссылки авторизации: например, у Google нужно
    # явно просить согласие, иначе он не покажет экран выбора аккаунта.
    authorize_params: dict[str, str] = field(default_factory=dict)


PROVIDERS: dict[str, ProviderConfig] = {
    YANDEX: ProviderConfig(
        name=YANDEX,
        title='Яндекс ID',
        authorize_url='https://oauth.yandex.ru/authorize',
        token_url='https://oauth.yandex.ru/token',  # noqa: S106 — адрес, а не секрет
        userinfo_url='https://login.yandex.ru/info?format=json',
        scope='login:email login:info',
        parse=parse_yandex,
    ),
    GOOGLE: ProviderConfig(
        name=GOOGLE,
        title='Google',
        authorize_url='https://accounts.google.com/o/oauth2/v2/auth',
        token_url='https://oauth2.googleapis.com/token',  # noqa: S106 — адрес, а не секрет
        userinfo_url='https://openidconnect.googleapis.com/v1/userinfo',
        scope='openid email profile',
        parse=parse_google,
        authorize_params={'prompt': 'select_account'},
    ),
}


@dataclass(frozen=True)
class ProviderCredentials:
    """Ключи приложения, выданные поставщиком при регистрации."""

    client_id: str
    client_secret: str


class AuthlibProvider(OAuthProvider):
    """Поставщик OAuth 2.0 поверх Authlib."""

    def __init__(
        self,
        config: ProviderConfig,
        credentials: ProviderCredentials,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.config = config
        self._credentials = credentials
        self._timeout = timeout
        # Транспорт подменяют тесты: так проверяется работа с поставщиком, а не сеть.
        self._transport = transport

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def title(self) -> str:
        return self.config.title

    async def authorization_url(self, state: str, redirect_uri: str) -> str:
        """Ссылка, по которой пользователь идёт к поставщику.

        state возвращается поставщиком без изменений — по нему мы узнаём свой
        же начатый вход и отвергаем чужие ответы. Сеть здесь не нужна, ссылка
        собирается на месте.
        """
        async with self._client(redirect_uri) as client:
            url, _ = client.create_authorization_url(
                self.config.authorize_url, state=state, **self.config.authorize_params,
            )
        return url

    async def fetch_profile(self, code: str, redirect_uri: str) -> SocialProfile:
        """Меняет код на токен и читает им данные пользователя.

        Raises:
            ProviderRejectedError: поставщик не принял код.
            ProviderUnavailableError: поставщик не ответил.
        """
        async with self._client(redirect_uri) as client:
            try:
                await client.fetch_token(self.config.token_url, code=code)
                response = await client.get(self.config.userinfo_url)
                response.raise_for_status()
            except OAuthError as exc:
                # Код просрочен, уже использован или не наш: вход не состоялся.
                logger.warning('Поставщик %s не принял код: %s', self.name, exc)
                raise ProviderRejectedError(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise self._status_error(exc) from exc
            except httpx.HTTPError as exc:
                logger.warning('Поставщик %s не ответил: %s', self.name, exc)
                raise ProviderUnavailableError(str(exc)) from exc
            return self.config.parse(response.json())

    def _status_error(self, exc: httpx.HTTPStatusError) -> Exception:
        """4xx — поставщик отказал, 5xx — у него сбой: реакция на них разная."""
        status = exc.response.status_code
        logger.warning('Поставщик %s ответил %s', self.name, status)
        if status < httpx.codes.INTERNAL_SERVER_ERROR:
            return ProviderRejectedError(f'status {status}')
        return ProviderUnavailableError(f'status {status}')

    def _client(self, redirect_uri: str) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            client_id=self._credentials.client_id,
            client_secret=self._credentials.client_secret,
            scope=self.config.scope,
            redirect_uri=redirect_uri,
            timeout=self._timeout,
            transport=self._transport,
        )
