"""Соседние сервисы кинотеатра по HTTP: каталог, справочник имён, уведомления.

Общее у всех трёх вынесено в `HttpService`, чтобы поведение при отказе соседа
было одинаковым и не расходилось при правке одного из адаптеров:

* явный таймаут на запрос (задаётся клиенту httpx в точке сборки);
* прерыватель: после серии сбоев запросы к упавшему сервису не уходят вовсе
  и сразу получают отказ, а не ждут таймаута;
* `X-Request-Id` едет дальше — по нему запрос собирается в журналах и Jaeger;
* сбой (нет соединения, таймаут, 5xx) выходит наружу как
  `StorageUnavailableError`, а 4xx — это ответ, а не сбой: его разбирает
  конкретный адаптер.

Повторов здесь нет намеренно. Запросы идут на пути пользователя с пределом в
300 мс; повтор после таймаута только удвоил бы ожидание. Повторяет
ретранслятор outbox — на своей паузе.
"""

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import httpx

from core.request_id import HEADER as REQUEST_ID_HEADER
from core.request_id import get_request_id
from models.domain import Film
from storage.base import Catalog, EventRejectedError, NotificationGateway, People, StorageUnavailableError
from storage.resilience import CircuitBreaker

logger = logging.getLogger(__name__)

# Линтер принимает имя заголовка за пароль из-за слова token.
SERVICE_TOKEN_HEADER = 'X-Service-Token'  # noqa: S105


class HttpService:
    """Вызов соседнего сервиса с прерывателем и переводом сбоев в контракт хранилища."""

    name = 'service'

    def __init__(self, client: httpx.AsyncClient, breaker: CircuitBreaker | None = None) -> None:
        self._client = client
        self._breaker = breaker or CircuitBreaker(failures=5, reset_timeout=30.0)

    async def _request(
        self, method: str, path: str, headers: dict[str, str] | None = None, **kwargs: Any,
    ) -> httpx.Response:
        """Ответ соседа с любым статусом ниже 500."""
        if not self._breaker.allows():
            raise StorageUnavailableError(f'{self.name}: отключён прерывателем')
        request_headers = {REQUEST_ID_HEADER: get_request_id(), **(headers or {})}
        try:
            response = await self._client.request(method, path, headers=request_headers, **kwargs)
        except httpx.HTTPError as error:
            self._breaker.record_failure()
            raise StorageUnavailableError(f'{self.name} недоступен: {error!r}') from error
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            self._breaker.record_failure()
            raise StorageUnavailableError(f'{self.name} ответил {response.status_code}')
        self._breaker.record_success()
        return response


class HttpCatalog(HttpService, Catalog):
    """Каталог фильмов — Async API."""

    name = 'Async API'

    async def film(self, film_id: UUID, authorization: str | None) -> Film | None:
        headers = {'Authorization': authorization} if authorization else {}
        response = await self._request('GET', f'/api/v1/films/{film_id}', headers=headers)
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code == httpx.codes.FORBIDDEN:
            # Фильм только по подписке, а у хоста её нет: для него фильма нет.
            return None
        if response.is_error:
            # Остальные 4xx — наш запрос сломан (не тот токен, не тот адрес).
            raise StorageUnavailableError(f'{self.name} отказал: {response.status_code}')
        body = response.json()
        return Film(
            id=UUID(body['uuid']), title=body['title'], type=body.get('type'), poster_url=body.get('poster_url'),
        )


class HttpPeople(HttpService, People):
    """Справочник контактов сервиса авторизации: имя для страниц и писем."""

    name = 'Сервис авторизации'
    CONTACTS_PATH = '/auth/api/v1/users/contacts'

    def __init__(self, client: httpx.AsyncClient, service_token: str, breaker: CircuitBreaker | None = None) -> None:
        super().__init__(client, breaker)
        self._service_token = service_token

    async def names(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        if not user_ids:
            return {}
        response = await self._request(
            'POST', self.CONTACTS_PATH,
            headers={SERVICE_TOKEN_HEADER: self._service_token},
            json={'user_ids': [str(user_id) for user_id in user_ids]},
        )
        if response.is_error:
            raise StorageUnavailableError(f'Справочник контактов отказал: {response.status_code}')
        return {UUID(item['id']): display_name(item) for item in response.json()}


def display_name(contact: dict[str, Any]) -> str:
    """Имя и фамилия, если зритель их указал, иначе логин."""
    full = ' '.join(part for part in (contact.get('first_name'), contact.get('last_name')) if part)
    return full or contact.get('login') or ''


class HttpNotifications(HttpService, NotificationGateway):
    """API уведомлений."""

    name = 'Сервис уведомлений'
    EVENTS_PATH = '/notify/api/v1/events'

    def __init__(self, client: httpx.AsyncClient, service_token: str, breaker: CircuitBreaker | None = None) -> None:
        super().__init__(client, breaker)
        self._service_token = service_token

    async def send(self, event_id: UUID, payload: dict, request_id: str) -> None:
        response = await self._request(
            'POST', self.EVENTS_PATH,
            # Идентификатор запроса — тот, что был у брони: письмо находится в
            # журналах по тому же request_id, что и действие гостя.
            headers={SERVICE_TOKEN_HEADER: self._service_token, REQUEST_ID_HEADER: request_id},
            json={'event_id': str(event_id), **payload},
        )
        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            # Не тот служебный секрет — это настройка стенда, а не событие:
            # исправят секрет, и события уйдут. Терять их нельзя.
            raise StorageUnavailableError(f'{self.name} не принял служебный секрет: {response.status_code}')
        if response.is_error:
            raise EventRejectedError(f'{response.status_code}: {response.text[:500]}')
