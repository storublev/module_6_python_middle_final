"""Справочник контактов: сервис авторизации по HTTP.

Воркеру приходит только `user_id` — адрес и имя он берёт здесь. Так прямо
требует чек-лист проектного задания: «к воркеру не придёт адрес email и ФИО
получателя… он должен сам сходить в сервис авторизации».

Два правила, ради которых этот адаптер выглядит именно так:

* **пачкой, а не по одному.** На тысячу адресатов — один запрос. Тысяча
  запросов на рассылку и есть тот отказ подсистемы данных, от которого
  предостерегает задача урока про RabbitMQ;
* **сбой не теряет письма.** Недоступность сервиса авторизации выходит наружу
  как `StorageUnavailableError`, воркер отправляет сообщение в отложенный
  повтор, и рассылка просто ждёт. Прерыватель не даёт долбиться в упавший
  сервис: пока он разомкнут, запросы даже не уходят.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

import httpx

from core.request_id import HEADER as REQUEST_ID_HEADER
from core.request_id import get_request_id
from models.notification import Recipient
from storage.base import ContactDirectory, StorageUnavailableError
from storage.resilience import CircuitBreaker

logger = logging.getLogger(__name__)

# Линтер принимает имя заголовка за пароль из-за слова token.
SERVICE_TOKEN_HEADER = 'X-Service-Token'  # noqa: S105
CONTACTS_PATH = '/auth/api/v1/users/contacts'


class AuthContactDirectory(ContactDirectory):
    """Контакты из сервиса авторизации."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        service_token: str,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._client = client
        self._service_token = service_token
        self._breaker = breaker or CircuitBreaker(failures=5, reset_timeout=30.0)

    async def contacts(self, user_ids: Sequence[UUID]) -> list[Recipient]:
        if not user_ids:
            return []
        body = await self._request(
            'POST', CONTACTS_PATH, json={'user_ids': [str(user_id) for user_id in user_ids]},
        )
        return [_recipient(item) for item in body]

    async def page(self, after_id: UUID | None, limit: int) -> tuple[list[Recipient], UUID | None]:
        params: dict[str, str | int] = {'limit': limit}
        if after_id is not None:
            params['after'] = str(after_id)
        body = await self._request('GET', CONTACTS_PATH, params=params)
        next_after = body.get('next_after')
        return [_recipient(item) for item in body.get('items', [])], UUID(next_after) if next_after else None

    async def _request(self, method: str, path: str, **kwargs: object):  # noqa: ANN003, ANN202
        if not self._breaker.allows():
            raise StorageUnavailableError('Сервис авторизации отключён прерывателем')
        headers = {
            SERVICE_TOKEN_HEADER: self._service_token,
            # Идентификатор запроса едет дальше: без него в общем журнале не
            # связать письмо с действием, которое его породило.
            REQUEST_ID_HEADER: get_request_id(),
        }
        try:
            response = await self._client.request(method, path, headers=headers, **kwargs)  # type: ignore[arg-type]
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            # 4xx — это наша ошибка (не тот секрет, не тот формат), и повторять
            # её бессмысленно: прерыватель на неё не реагирует.
            if error.response.status_code < httpx.codes.INTERNAL_SERVER_ERROR:
                raise StorageUnavailableError(
                    f'Справочник контактов отказал: {error.response.status_code}',
                ) from error
            self._breaker.record_failure()
            raise StorageUnavailableError(f'Сервис авторизации ответил {error.response.status_code}') from error
        except httpx.HTTPError as error:
            self._breaker.record_failure()
            raise StorageUnavailableError(f'Сервис авторизации недоступен: {error}') from error
        self._breaker.record_success()
        return response.json()


def _recipient(item: dict[str, str | None]) -> Recipient:
    return Recipient(
        user_id=UUID(str(item['id'])),
        email=item.get('email'),
        first_name=item.get('first_name'),
        last_name=item.get('last_name'),
        timezone=item.get('timezone'),
    )
