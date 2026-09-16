"""Заглушки сети и сервиса авторизации для модульных тестов."""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import requests

from users.auth_client import AuthProfile


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
    """Сервис авторизации, отвечающий заданными значениями."""

    token: str = 'access-token'
    profile: AuthProfile | None = None
    allowed: bool = True
    login_error: Exception | None = None
    profile_error: Exception | None = None
    logged_out: list[str] = field(default_factory=list)

    def login(self, login: str, password: str) -> str:
        if self.login_error:
            raise self.login_error
        return self.token

    def get_profile(self, access_token: str) -> AuthProfile:
        if self.profile_error:
            raise self.profile_error
        assert self.profile is not None
        return self.profile

    def check_permission(self, access_token: str, permission: str) -> bool:
        if self.profile_error:
            raise self.profile_error
        return self.allowed

    def logout(self, access_token: str) -> None:
        self.logged_out.append(access_token)


def iter_calls(session: FakeSession) -> Iterable[str]:
    """Пути запросов, которые сделал клиент."""
    return (call.url for call in session.requests_log)
