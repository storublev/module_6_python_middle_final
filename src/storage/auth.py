"""Проверка прав в сервисе авторизации по HTTP.

Сервис авторизации — самая нагруженная часть сайта, и он может отказать.
Поэтому у запроса явные таймауты, повторяется только обрыв соединения, а серия
сбоев размыкает прерыватель: каталог перестаёт ждать сервис и отдаёт публичный
контент. Все сбои выходят наружу как AccessUnavailableError — httpx за
пределы этого модуля не протекает.

Права спрашиваются с `fresh=true`, то есть по базе, минуя кеш сервиса
авторизации: отозванная подписка должна закрывать доступ сразу. Свой кеш
ответов здесь не заводится намеренно — он вернул бы ту же задержку отзыва,
от которой уходим.
"""

import logging
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any, TypeVar

import backoff
import httpx

from storage.access import AccessGateway, AccessUnavailableError, TokenRejectedError
from storage.resilience import BackoffPolicy, CircuitBreaker

logger = logging.getLogger(__name__)

T = TypeVar('T')

CHECK_PATH = '/auth/api/v1/access/check'
UNKNOWN_ERROR = 'token rejected by auth service'

# Повторять стоит только то, что заведомо не дошло до сервиса: соединение не
# установилось. Таймаут ответа не повторяется — сервис уже занят запросом,
# а повтор добавил бы ему нагрузки ровно тогда, когда ему тяжело.
RETRYABLE_ERRORS = (httpx.ConnectError,)


class AuthAccessGateway(AccessGateway):
    """Права пользователя из сервиса авторизации."""

    def __init__(self, client: httpx.AsyncClient, retry: BackoffPolicy, breaker: CircuitBreaker):
        self._client = client
        self._breaker = breaker
        self._call_with_retry = backoff.on_exception(
            backoff.expo,
            RETRYABLE_ERRORS,
            max_time=retry.max_time,
            factor=retry.factor,
            max_value=retry.max_value,
            logger=logger,
            backoff_log_level=logging.WARNING,
        )(self._call)

    async def check(self, token: str, permission: str) -> bool:
        if not self._breaker.allows():
            raise AccessUnavailableError('circuit breaker is open')
        try:
            response = await self._call_with_retry(
                self._client.get,
                url=CHECK_PATH,
                params={'permission': permission, 'fresh': 'true'},
                headers={'Authorization': f'Bearer {token}'},
            )
        except httpx.HTTPError as exc:
            self._fail(str(exc))
            raise AccessUnavailableError(str(exc)) from exc

        if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            self._fail(f'ответ {response.status_code}')
            raise AccessUnavailableError(f'status {response.status_code}')

        # Ответ получен, значит сервис жив: отказ в правах сбоем не считается.
        self._breaker.record_success()
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            raise TokenRejectedError(*self._error(response))
        if response.status_code != HTTPStatus.OK:
            raise AccessUnavailableError(f'unexpected status {response.status_code}')
        return bool(response.json()['allowed'])

    def _fail(self, reason: str) -> None:
        """Отмечает сбой в прерывателе и пишет причину в журнал."""
        self._breaker.record_failure()
        if self._breaker.is_open:
            logger.warning('Сервис авторизации недоступен (%s); прерыватель открыт, права не спрашиваем', reason)
        else:
            logger.warning('Сервис авторизации недоступен: %s', reason)

    @staticmethod
    def _error(response: httpx.Response) -> tuple[str, str]:
        """Код и текст ошибки из ответа сервиса авторизации."""
        try:
            payload = response.json()
        except ValueError:
            return '', UNKNOWN_ERROR
        return str(payload.get('code', '')), str(payload.get('detail', UNKNOWN_ERROR))

    @staticmethod
    async def _call(method: Callable[..., Awaitable[T]], **params: Any) -> T:
        """Вызывает метод клиента и дожидается ответа.

        Такая же обёртка, как у хранилища Elasticsearch: backoff должен видеть
        настоящую корутинную функцию, иначе повторял бы создание корутины,
        а ошибка возникала бы при await, вне повторов.
        """
        return await method(**params)
