"""Вызов внутреннего API кинотеатра из интерфейса.

Все клиенты устроены одинаково, поэтому общее вынесено сюда:

* `X-Request-Id` страницы едет в каждый вызов — по нему страница собирается
  в журналах и в Jaeger вместе со всеми походами в API;
* ответ 4xx — это ответ: он превращается в `ApiError` с машиночитаемым кодом,
  по которому страница покажет человеку понятный текст;
* нет соединения, таймаут или 5xx — `BackendUnavailableError`: страница покажет
  блок «временно недоступно», а не упадёт целиком.
"""

from typing import Any

import httpx

from core.request_id import HEADER as REQUEST_ID_HEADER
from core.request_id import get_request_id


class BackendUnavailableError(Exception):
    """API не ответило или ответило 5xx."""


class ApiError(Exception):
    """API отказало по существу (4xx)."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(f'{status} {code}: {detail}')
        self.status = status
        self.code = code
        self.detail = detail


class ApiClient:
    name = 'API'

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _call(self, method: str, path: str, token: str | None = None, **kwargs: Any) -> Any:
        headers = {REQUEST_ID_HEADER: get_request_id()}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        try:
            response = await self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise BackendUnavailableError(f'{self.name}: {error!r}') from error
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise BackendUnavailableError(f'{self.name} ответил {response.status_code}')
        if response.is_error:
            raise _api_error(response)
        if response.status_code == httpx.codes.NO_CONTENT:
            return None
        return response.json()


def _api_error(response: httpx.Response) -> ApiError:
    """Код и текст ошибки из ответа в любом из форматов кинотеатра."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get('detail') if isinstance(body, dict) else None
    if isinstance(detail, list):
        # 422 FastAPI: список ошибок полей — для человека хватит первой.
        first = detail[0] if detail else {}
        return ApiError(response.status_code, 'validation_error', str(first.get('msg', 'Invalid data')))
    code = body.get('code') if isinstance(body, dict) else None
    return ApiError(response.status_code, code or f'http_{response.status_code}', str(detail or response.reason_phrase))
