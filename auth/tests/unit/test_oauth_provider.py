"""Поставщик OAuth на Authlib: ссылка авторизации, обмен кода и разбор ответов."""

from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx
import pytest

from storage.base import ProviderRejectedError, ProviderUnavailableError
from storage.oauth import (
    GOOGLE,
    PROVIDERS,
    YANDEX,
    AuthlibProvider,
    ProviderCredentials,
    parse_google,
    parse_yandex,
)

CREDENTIALS = ProviderCredentials(client_id='app-id', client_secret='app-secret')
REDIRECT_URI = 'http://localhost/auth/api/v1/oauth/yandex/callback'
STATE = 'random-state'
CODE = 'one-time-code'
TOKEN_RESPONSE = {'access_token': 'provider-token', 'token_type': 'Bearer'}
YANDEX_INFO = {'id': '42', 'login': 'neo', 'real_name': 'Neo', 'default_email': 'neo@example.com'}


def build_provider(handler, name: str = YANDEX) -> AuthlibProvider:
    """Поставщик с подменённым транспортом: сети нет, поведение Authlib настоящее."""
    return AuthlibProvider(PROVIDERS[name], CREDENTIALS, timeout=1, transport=httpx.MockTransport(handler))


def answer(token_status: int = HTTPStatus.OK, info_status: int = HTTPStatus.OK, info=None):
    """Отвечает на обмен кода и на запрос данных пользователя."""

    def handler(request: httpx.Request) -> httpx.Response:
        if 'token' in request.url.path:
            return httpx.Response(token_status, json=TOKEN_RESPONSE if token_status == HTTPStatus.OK else {})
        return httpx.Response(info_status, json=info if info is not None else YANDEX_INFO)

    return handler


# Ссылка авторизации

async def test_authorization_url_leads_to_provider():
    """Пользователь уходит на страницу входа поставщика."""
    url = await build_provider(answer()).authorization_url(STATE, REDIRECT_URI)

    assert url.startswith(PROVIDERS[YANDEX].authorize_url)


@pytest.mark.parametrize(
    'param, expected',
    [
        pytest.param('state', STATE, id='state'),
        pytest.param('client_id', CREDENTIALS.client_id, id='client-id'),
        pytest.param('redirect_uri', REDIRECT_URI, id='redirect-uri'),
        pytest.param('response_type', 'code', id='authorization-code-flow'),
    ],
)
async def test_authorization_url_carries_parameters(param, expected):
    """В ссылке есть всё, что нужно поставщику, и наш state."""
    url = await build_provider(answer()).authorization_url(STATE, REDIRECT_URI)

    assert parse_qs(urlparse(url).query)[param] == [expected]


async def test_client_secret_does_not_leak_into_the_link():
    """Секрет приложения в браузер не уходит: он нужен только при обмене кода."""
    url = await build_provider(answer()).authorization_url(STATE, REDIRECT_URI)

    assert CREDENTIALS.client_secret not in url


# Обмен кода

async def test_profile_is_read_after_exchange():
    """Код меняется на токен, и им читаются данные пользователя."""
    profile = await build_provider(answer()).fetch_profile(CODE, REDIRECT_URI)

    assert (profile.social_id, profile.display_name, profile.email) == ('42', 'Neo', 'neo@example.com')


async def test_rejected_code_is_reported():
    """Поставщик не принял код — вход не состоялся."""
    provider = build_provider(answer(token_status=HTTPStatus.BAD_REQUEST))

    with pytest.raises(ProviderRejectedError):
        await provider.fetch_profile(CODE, REDIRECT_URI)


async def test_rejected_token_on_userinfo_is_reported():
    """Токен не подошёл к данным пользователя — тоже отказ, а не сбой."""
    provider = build_provider(answer(info_status=HTTPStatus.UNAUTHORIZED))

    with pytest.raises(ProviderRejectedError):
        await provider.fetch_profile(CODE, REDIRECT_URI)


async def test_provider_error_is_unavailability():
    """5xx у поставщика — его сбой: запрос стоит повторить позже."""
    provider = build_provider(answer(info_status=HTTPStatus.INTERNAL_SERVER_ERROR))

    with pytest.raises(ProviderUnavailableError):
        await provider.fetch_profile(CODE, REDIRECT_URI)


async def test_network_failure_is_unavailability():
    """Сбой сети наружу выходит ошибкой поставщика: httpx за пределы адаптера не протекает."""

    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('connection refused')

    with pytest.raises(ProviderUnavailableError):
        await build_provider(fail).fetch_profile(CODE, REDIRECT_URI)


# Разбор данных пользователя

def test_yandex_profile_is_parsed():
    """Ответ Яндекса разбирается в идентификатор, имя и почту."""
    profile = parse_yandex(YANDEX_INFO)

    assert (profile.social_id, profile.display_name, profile.email) == ('42', 'Neo', 'neo@example.com')


def test_yandex_profile_without_real_name_falls_back_to_login():
    """Имя не заполнено — показываем логин: в личном кабинете должно быть что-то узнаваемое."""
    profile = parse_yandex({'id': '42', 'login': 'neo'})

    assert profile.display_name == 'neo'


def test_google_profile_is_identified_by_sub():
    """Google опознаётся по sub: email у него сменить можно, sub — нет."""
    profile = parse_google({'sub': '108', 'name': 'Neo', 'email': 'neo@example.com'})

    assert profile.social_id == '108'


def test_google_scope_requests_openid():
    """У Google запрашивается openid: вход через него — это OpenID Connect."""
    assert 'openid' in PROVIDERS[GOOGLE].scope
