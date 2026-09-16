"""Клиент сервиса авторизации: вход сотрудника и проверка его права на админку.

Сервис авторизации — внешняя зависимость, и он может быть недоступен, поэтому
у каждого запроса явный таймаут, обрыв соединения повторяется с растущей
паузой, а серия сбоев размыкает прерыватель (circuit_breaker.py). Все отказы
превращаются в исключения этого модуля: HTTP-подробности и requests наружу не
протекают — бэкенд аутентификации работает с AuthClient, а не с сетью.

Таймаут ответа не повторяется: сервис, не ответивший за отведённое время,
может уже обрабатывать запрос, а повтор входа съел бы лимит попыток.
"""

import logging
from dataclasses import dataclass
from http import HTTPStatus
from uuid import UUID

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from users.circuit_breaker import CircuitBreaker
from users.request_id import HEADER as REQUEST_ID_HEADER
from users.request_id import NO_REQUEST, get_request_id

logger = logging.getLogger(__name__)

API_PREFIX = '/auth/api/v1'


class AuthServiceError(Exception):
    """Обращение к сервису авторизации не удалось."""


class AuthServiceUnavailableError(AuthServiceError):
    """Сервис авторизации не ответил: сбой сети, таймаут, 5xx или открытый прерыватель."""


class InvalidCredentialsError(AuthServiceError):
    """Сервис авторизации не признал логин и пароль."""


class TooManyRequestsError(AuthServiceError):
    """Исчерпан лимит попыток входа."""


@dataclass(frozen=True)
class AuthProfile:
    """Данные сотрудника, полученные от сервиса авторизации."""

    id: UUID
    login: str
    is_superuser: bool


class AuthClient:
    def __init__(
        self,
        base_url: str,
        connect_timeout: float,
        read_timeout: float,
        connect_retries: int,
        backoff_factor: float,
        breaker: CircuitBreaker,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip('/')
        self._timeout = (connect_timeout, read_timeout)
        self._breaker = breaker
        # Готовую сессию передают тесты: так проверяется поведение клиента,
        # а не сеть.
        self._session = session or self._build_session(connect_retries, backoff_factor)

    @staticmethod
    def _build_session(connect_retries: int, backoff_factor: float) -> requests.Session:
        session = requests.Session()
        adapter = HTTPAdapter(
            max_retries=Retry(
                total=connect_retries,
                # Повторяем только то, что заведомо не дошло до сервиса:
                # соединение не установилось. read=0 — таймаут ответа не повторяем.
                connect=connect_retries,
                read=0,
                status=0,
                backoff_factor=backoff_factor,
                # Случайная добавка разводит воркеры, начавшие повтор одновременно.
                backoff_jitter=backoff_factor,
                allowed_methods=None,
                raise_on_status=False,
            ),
        )
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session

    def login(self, login: str, password: str) -> str:
        """Меняет логин и пароль на access-токен.

        Пароль уходит только в теле запроса и в журнал не попадает.
        """
        response = self._request('POST', '/login', json={'login': login, 'password': password})
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            raise InvalidCredentialsError(self._error_code(response))
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise TooManyRequestsError(self._error_code(response))
        self._raise_for_unexpected(response, HTTPStatus.OK)
        return response.json()['access_token']

    def get_profile(self, access_token: str) -> AuthProfile:
        """Читает данные владельца токена."""
        response = self._request('GET', '/users/me', token=access_token)
        self._raise_for_unexpected(response, HTTPStatus.OK)
        payload = response.json()
        return AuthProfile(
            id=UUID(payload['id']),
            login=payload['login'],
            is_superuser=bool(payload['is_superuser']),
        )

    def check_permission(self, access_token: str, permission: str) -> bool:
        """Есть ли у владельца токена право.

        Права читаются из базы, минуя кеш (fresh=true): отозванный доступ к
        админке должен закрываться сразу, а не через время жизни кеша.
        """
        response = self._request(
            'GET', '/access/check', token=access_token, params={'permission': permission, 'fresh': 'true'},
        )
        self._raise_for_unexpected(response, HTTPStatus.OK)
        return bool(response.json()['allowed'])

    def logout(self, access_token: str) -> None:
        """Закрывает сессию в сервисе авторизации.

        Дальше сотрудника пускает сессия Django, а токены не нужны и хранить их
        негде: незакрытая сессия осталась бы висеть до истечения refresh-токена.
        Не получилось — не беда, поэтому ошибки только в журнал.
        """
        try:
            response = self._request('POST', '/logout', token=access_token)
        except AuthServiceError as exc:
            logger.warning('Не удалось закрыть сессию в сервисе авторизации: %s', exc)
            return
        if response.status_code != HTTPStatus.NO_CONTENT:
            logger.warning('Сервис авторизации не закрыл сессию: %s', response.status_code)

    def _request(self, method: str, path: str, token: str | None = None, **kwargs) -> requests.Response:
        if not self._breaker.allows():
            raise AuthServiceUnavailableError('circuit breaker is open')
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        # Идентификатор запроса идёт дальше по цепочке: вход сотрудника и
        # вызванная им проверка права собираются в одну историю.
        request_id = get_request_id()
        if request_id != NO_REQUEST:
            headers[REQUEST_ID_HEADER] = request_id
        try:
            response = self._session.request(
                method, f'{self._base_url}{API_PREFIX}{path}', headers=headers, timeout=self._timeout, **kwargs,
            )
        except requests.RequestException as exc:
            self._breaker.record_failure()
            raise AuthServiceUnavailableError(str(exc)) from exc
        # Ошибка на стороне сервиса — тоже его недоступность: пробовать
        # следующие запросы цепочки входа незачем.
        if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            self._breaker.record_failure()
            raise AuthServiceUnavailableError(f'status {response.status_code}')
        self._breaker.record_success()
        return response

    @staticmethod
    def _error_code(response: requests.Response) -> str:
        """Машиночитаемый код ошибки из ответа сервиса авторизации."""
        try:
            return str(response.json().get('code', ''))
        except ValueError:
            return ''

    @staticmethod
    def _raise_for_unexpected(response: requests.Response, expected: HTTPStatus) -> None:
        if response.status_code != expected:
            raise AuthServiceError(f'unexpected status {response.status_code}')


def build_client() -> AuthClient:
    """Собирает клиент по настройкам Django — единственное место, где он создаётся."""
    from django.conf import settings as django_settings

    config = django_settings.APP_SETTINGS
    return AuthClient(
        base_url=config.auth_api_url,
        connect_timeout=config.auth_connect_timeout,
        read_timeout=config.auth_read_timeout,
        connect_retries=config.auth_connect_retries,
        backoff_factor=config.auth_backoff_factor,
        breaker=CircuitBreaker(
            failures=config.auth_breaker_failures,
            reset_timeout=config.auth_breaker_reset_timeout,
        ),
    )
