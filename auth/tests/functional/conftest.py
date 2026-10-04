"""Общие фикстуры функциональных тестов.

HTTP-клиент и соединения с PostgreSQL и Redis создаются один раз на сессию.
Перед каждым тестом удаляются пользователи (вместе с их ролями и историей
входов), созданные тестами роли, задания на сброс кеша прав и всё в Redis —
тесты не видят чужих данных. Роль subscribers создаёт миграция, её тесты не меняют.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis

from tests.functional.settings import settings

REQUEST_ID_HEADER = 'X-Request-Id'

API = '/auth/api/v1'
PASSWORD = 'followtherabbit'
SEEDED_ROLE = 'subscribers'


@dataclass
class Account:
    """Пользователь, вошедший в систему: его id, логин и пара токенов."""

    id: str
    login: str
    password: str
    access_token: str
    refresh_token: str

    @property
    def headers(self) -> dict[str, str]:
        return bearer(self.access_token)


def bearer(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture(scope='session')
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # Сервис требует идентификатор запроса: в работе его ставит nginx, здесь —
    # тесты. Так они ходят к сервису теми же запросами, что и настоящие клиенты.
    headers = {REQUEST_ID_HEADER: 'functional-tests'}
    async with httpx.AsyncClient(base_url=f'{settings.service_url}{API}', timeout=10, headers=headers) as http:
        yield http


@pytest.fixture(scope='session')
async def pg() -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(settings.postgres_dsn)
    yield connection
    await connection.close()


@pytest.fixture(scope='session')
async def redis_client() -> AsyncIterator[Redis]:
    client = Redis(host=settings.redis_host, port=settings.redis_port)
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
async def clean_storage(pg: asyncpg.Connection, redis_client: Redis) -> None:
    """Перед каждым тестом удаляет пользователей, созданные тестами роли, задания на сброс кеша и сессии."""
    await pg.execute('TRUNCATE auth.users CASCADE')
    await pg.execute('DELETE FROM auth.roles WHERE name <> $1', SEEDED_ROLE)
    await pg.execute('TRUNCATE auth.access_invalidations')
    await redis_client.flushdb()


async def signup(client: httpx.AsyncClient, login: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post('/signup', json={'login': login, 'password': password})
    assert response.status_code == 201, response.text
    return response.json()


async def login(client: httpx.AsyncClient, login: str, password: str = PASSWORD, **kwargs: Any) -> dict[str, Any]:
    response = await client.post('/login', json={'login': login, 'password': password}, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


MakeAccount = Callable[..., Awaitable[Account]]


@pytest.fixture
def make_account(client: httpx.AsyncClient, pg: asyncpg.Connection) -> MakeAccount:
    """Регистрирует пользователя и входит под ним.

    Суперпользователя создаёт консольная команда, у тестов доступа к ней нет,
    поэтому признак выставляется прямо в базе.
    """

    async def inner(name: str = 'neo', password: str = PASSWORD, superuser: bool = False) -> Account:
        user = await signup(client, name, password)
        if superuser:
            await pg.execute('UPDATE auth.users SET is_superuser = true WHERE id = $1', user['id'])
        tokens = await login(client, name, password)
        return Account(user['id'], user['login'], password, tokens['access_token'], tokens['refresh_token'])

    return inner


@pytest.fixture
async def neo(make_account: MakeAccount) -> Account:
    return await make_account('neo')


@pytest.fixture
async def admin(make_account: MakeAccount) -> Account:
    return await make_account('admin', superuser=True)


@pytest.fixture
async def subscribers_id(pg: asyncpg.Connection) -> str:
    return str(await pg.fetchval('SELECT id FROM auth.roles WHERE name = $1', SEEDED_ROLE))
