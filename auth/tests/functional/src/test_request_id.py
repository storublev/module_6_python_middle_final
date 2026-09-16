"""Идентификатор запроса: приём, возврат и требование заголовка.

Идентификатор выдаёт nginx и передаёт заголовком `X-Request-Id`. Сервис пишет
его в журнал, помечает им спан трассировки и возвращает клиенту, а запрос без
него отклоняет: такой запрос пришёл мимо шлюза и не найдётся ни в журналах,
ни в Jaeger.
"""

from http import HTTPStatus

import httpx
import pytest

from tests.functional.conftest import REQUEST_ID_HEADER
from tests.functional.settings import settings

API = '/auth/api/v1'
REQUEST_ID = 'test-request-id-42'


@pytest.fixture
async def bare_client():
    """Клиент без общих заголовков: общий шлёт X-Request-Id за нас."""
    async with httpx.AsyncClient(base_url=settings.service_url, timeout=10) as client:
        yield client


async def test_request_id_comes_back(client):
    """Идентификатор возвращается клиенту: его можно назвать в обращении в поддержку."""
    response = await client.get('/oauth/providers', headers={REQUEST_ID_HEADER: REQUEST_ID})

    assert response.headers[REQUEST_ID_HEADER] == REQUEST_ID


async def test_request_without_id_is_rejected(bare_client):
    """Запрос мимо шлюза отклоняется: без идентификатора его не найти."""
    response = await bare_client.get(f'{API}/oauth/providers')

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()['code'] == 'request_id_required'


async def test_rejection_names_the_header(bare_client):
    """В отказе сказано, какого заголовка не хватает."""
    response = await bare_client.get(f'{API}/oauth/providers')

    assert REQUEST_ID_HEADER in response.json()['detail']


async def test_documentation_does_not_require_the_header(bare_client):
    """С документации заголовок не спрашивается: её открывают браузером и проверкой живости."""
    response = await bare_client.get('/auth/api/openapi.json')

    assert response.status_code == HTTPStatus.OK


async def test_login_is_traced_with_its_own_id(client):
    """Идентификатор своего запроса не подменяется чужим: у каждого запроса он свой."""
    first = await client.get('/oauth/providers', headers={REQUEST_ID_HEADER: 'first'})
    second = await client.get('/oauth/providers', headers={REQUEST_ID_HEADER: 'second'})

    assert (first.headers[REQUEST_ID_HEADER], second.headers[REQUEST_ID_HEADER]) == ('first', 'second')
