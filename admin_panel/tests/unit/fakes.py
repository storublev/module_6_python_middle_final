"""Заглушки сети и сервиса авторизации для модульных тестов."""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import requests

from users.auth_client import AuthProfile, SessionExpiredError, Tokens


def make_response(status_code: int, payload: Any = None) -> requests.Response:
    """Ответ сервиса, как если бы его вернул requests."""
    response = requests.Response()
    response.status_code = status_code
    # requests собирает тело из потока; в тесте проще положить его напрямую.
    response._content = b'' if payload is None else json.dumps(payload).encode()
    response.headers['Content-Type'] = 'application/json'
    return response


@dataclass
class RecordedRequest:
    """Запрос, который клиент отправил бы в сеть."""

    method: str
    url: str
    headers: dict[str, str]
    kwargs: dict[str, Any]


@dataclass
class FakeSession:
    """Сессия requests, отвечающая заранее заданным списком ответов.

    Элемент списка — либо ответ, либо исключение: так проверяются и коды
    ошибок сервиса, и обрывы соединения.
    """

    replies: list[Any]
    requests_log: list[RecordedRequest] = field(default_factory=list)

    def request(self, method: str, url: str, headers: dict[str, str] | None = None, **kwargs) -> requests.Response:
        self.requests_log.append(RecordedRequest(method, url, dict(headers or {}), kwargs))
        if not self.replies:
            raise AssertionError(f'неожиданный запрос {method} {url}')
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@dataclass
class FakeAuthClient:
    """Сервис авторизации, отвечающий заданными значениями.

    Ошибки задаются полями: так проверяется, что делает админка, когда сервис
    не отвечает, не признаёт токен или отказывает в праве.
    """

    tokens: Tokens = field(default_factory=lambda: Tokens(access='access-token', refresh='refresh-token'))
    profile: AuthProfile | None = None
    allowed: bool = True
    login_error: Exception | None = None
    profile_error: Exception | None = None
    refresh_error: Exception | None = None
    logout_error: Exception | None = None
    logged_out: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    permission_checks: list[str] = field(default_factory=list)
    # Токены, которые клиент выдаст на обновление пары.
    next_tokens: Tokens | None = None

    @property
    def token(self) -> str:
        return self.tokens.access

    def login(self, login: str, password: str) -> Tokens:
        if self.login_error:
            raise self.login_error
        return self.tokens

    def refresh(self, refresh_token: str) -> Tokens:
        self.refreshed.append(refresh_token)
        if self.refresh_error:
            raise self.refresh_error
        self.tokens = self.next_tokens or Tokens(access='new-access-token', refresh='new-refresh-token')
        return self.tokens

    def get_profile(self, access_token: str) -> AuthProfile:
        self._check_token(access_token)
        assert self.profile is not None
        return self.profile

    def check_permission(self, access_token: str, permission: str) -> bool:
        self._check_token(access_token)
        self.permission_checks.append(permission)
        return self.allowed

    def logout(self, access_token: str) -> None:
        if self.logout_error:
            raise self.logout_error
        self.logged_out.append(access_token)

    def _check_token(self, access_token: str) -> None:
        if self.profile_error:
            raise self.profile_error
        if access_token != self.tokens.access:
            # Так ведёт себя сервис: по устаревшему токену он отвечает 401.
            raise SessionExpiredError('token_expired')


def iter_calls(session: FakeSession) -> Iterable[str]:
    """Пути запросов, которые сделал клиент."""
    return (call.url for call in session.requests_log)
