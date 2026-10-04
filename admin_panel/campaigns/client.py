"""Клиент сервиса уведомлений.

Админка **не ходит в базу уведомлений напрямую**. Причина та же, по которой
события идут через API: сервис уведомлений — центральный узел, и знание о том,
как устроены его таблицы, не должно расползаться по кинотеатру. Меняется схема
— меняется один сервис, а не два.

Своего пользователя у админки в сервисе уведомлений нет, поэтому она
опознаётся общим секретом в заголовке `X-Service-Token`. Все отказы
превращаются в исключения этого модуля: HTTP-подробности и requests наружу не
протекают — так же устроен клиент сервиса авторизации (`users/auth_client.py`).
"""

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import requests

from users.request_id import HEADER as REQUEST_ID_HEADER
from users.request_id import get_request_id

logger = logging.getLogger(__name__)

API_PREFIX = '/notify/api/v1'
SERVICE_TOKEN_HEADER = 'X-Service-Token'  # noqa: S105 - имя заголовка, а не пароль


class NotifyServiceError(Exception):
    """Обращение к сервису уведомлений не удалось."""


class NotifyUnavailableError(NotifyServiceError):
    """Сервис уведомлений не ответил: сбой сети, таймаут или 5xx."""


class NotifyRejectedError(NotifyServiceError):
    """Сервис уведомлений отказал по существу: негодный шаблон, занятый код.

    Текст ошибки приходит из сервиса и показывается менеджеру: это он ошибся в
    шаблоне, и починить может только он.
    """


@dataclass(frozen=True)
class NotifyClient:
    """Шаблоны и рассылки в сервисе уведомлений."""

    base_url: str
    service_token: str
    connect_timeout: float = 1.0
    read_timeout: float = 5.0

    def list_templates(self) -> list[dict[str, Any]]:
        return self._request('GET', '/templates')

    def get_template(self, code: str) -> dict[str, Any]:
        return self._request('GET', f'/templates/{code}')

    def create_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('POST', '/templates', json=payload)

    def update_template(self, code: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('PUT', f'/templates/{code}', json=payload)

    def delete_template(self, code: str) -> None:
        self._request('DELETE', f'/templates/{code}')

    def preview_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Показывает письмо на тестовых данных, ничего не сохраняя.

        Менеджер обязан посмотреть письмо до того, как оно уйдёт миллиону
        зрителей: отозвать его будет нельзя.
        """
        return self._request('POST', '/templates/preview', json=payload)

    def list_campaigns(self) -> list[dict[str, Any]]:
        return self._request('GET', '/campaigns')

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('POST', '/campaigns', json=payload)

    def run_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self._request('POST', f'/campaigns/{campaign_id}/run')

    def cancel_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self._request('POST', f'/campaigns/{campaign_id}/cancel')

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {
            SERVICE_TOKEN_HEADER: self.service_token,
            # Идентификатор запроса едет дальше: по нему в общем журнале
            # видно, что рассылку запустил именно этот заход в админку.
            REQUEST_ID_HEADER: get_request_id(),
        }
        url = f'{self.base_url.rstrip("/")}{API_PREFIX}{path}'
        try:
            response = requests.request(
                method, url, headers=headers, timeout=(self.connect_timeout, self.read_timeout), **kwargs,
            )
        except requests.RequestException as error:
            logger.warning('Сервис уведомлений недоступен: %s', error)
            raise NotifyUnavailableError(str(error)) from error

        if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise NotifyUnavailableError(f'Сервис уведомлений ответил {response.status_code}')
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            raise NotifyRejectedError(_detail_of(response))
        if response.status_code == HTTPStatus.NO_CONTENT or not response.content:
            return None
        return response.json()


def _detail_of(response: requests.Response) -> str:
    """Человекочитаемая причина отказа из ответа сервиса."""
    try:
        body = response.json()
    except ValueError:
        return f'HTTP {response.status_code}'
    if isinstance(body, dict) and 'detail' in body:
        return str(body['detail'])
    return f'HTTP {response.status_code}'
