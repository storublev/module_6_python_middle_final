"""Общие фикстуры функциональных тестов.

Клиенты Elasticsearch, Redis и HTTP создаются один раз на сессию: установка
соединений — самая дорогая часть подготовки. Индексы тоже создаются один раз,
а перед каждым тестом из них удаляются документы и сбрасывается кеш, чтобы
тесты не видели чужих данных.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from http import HTTPStatus
from typing import Any
from uuid import uuid4

import aiohttp
import pytest
from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk
from redis.asyncio import Redis

from tests.functional.settings import settings
from tests.functional.testdata.es_mapping import INDICES
from tests.functional.testdata.factories import Doc
from tests.functional.utils.helpers import Response

API_PREFIX = '/api/v1'
AUTH_PREFIX = '/auth/api/v1'


@pytest.fixture(scope='session')
async def es_client() -> AsyncIterator[AsyncElasticsearch]:
    client = AsyncElasticsearch(hosts=settings.elastic_url)
    yield client
    await client.close()


@pytest.fixture(scope='session')
async def redis_client() -> AsyncIterator[Redis]:
    client = Redis(host=settings.redis_host, port=settings.redis_port)
    yield client
    await client.aclose()


@pytest.fixture(scope='session')
async def http_session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


async def create_index(es_client: AsyncElasticsearch, index: str) -> None:
    await es_client.options(ignore_status=404).indices.delete(index=index)
    await es_client.indices.create(index=index, **INDICES[index])


@pytest.fixture(scope='session', autouse=True)
async def es_indices(es_client: AsyncElasticsearch) -> AsyncIterator[None]:
    """Создаёт индексы со схемой ETL на время сессии."""
    for index in INDICES:
        await create_index(es_client, index)
    yield
    await es_client.options(ignore_status=404).indices.delete(index=','.join(INDICES))


@pytest.fixture(autouse=True)
async def clean_storage(es_client: AsyncElasticsearch, redis_client: Redis, es_indices: None) -> None:
    """Перед каждым тестом очищает индексы и кеш."""
    await es_client.delete_by_query(
        index=','.join(INDICES),
        query={'match_all': {}},
        refresh=True,
        conflicts='proceed',
    )
    await redis_client.flushall()


@pytest.fixture
def es_write_data(es_client: AsyncElasticsearch) -> Callable[[str, Iterable[Doc]], Awaitable[None]]:
    """Записывает документы в индекс; сразу после записи они видны поиску."""

    async def inner(index: str, documents: Iterable[Doc]) -> None:
        actions = [{'_index': index, '_id': doc['id'], '_source': doc} for doc in documents]
        # refresh: без него документы появятся в поиске только через refresh_interval.
        _, errors = await async_bulk(es_client, actions, refresh=True, raise_on_error=False)
        if errors:
            raise RuntimeError(f'Ошибка записи данных в Elasticsearch: {errors}')

    return inner


@pytest.fixture
def es_delete_data(es_client: AsyncElasticsearch) -> Callable[[str, Iterable[Doc]], Awaitable[None]]:
    """Удаляет документы из индекса — например, чтобы убедиться, что ответ пришёл из кеша."""

    async def inner(index: str, documents: Iterable[Doc]) -> None:
        ids = [doc['id'] for doc in documents]
        await es_client.delete_by_query(index=index, query={'ids': {'values': ids}}, refresh=True)

    return inner


@pytest.fixture
async def drop_index(es_client: AsyncElasticsearch) -> AsyncIterator[Callable[[str], Awaitable[None]]]:
    """Удаляет индекс на время теста и восстанавливает его после."""
    dropped: list[str] = []

    async def inner(index: str) -> None:
        await es_client.indices.delete(index=index)
        dropped.append(index)

    yield inner
    for index in dropped:
        await create_index(es_client, index)


@pytest.fixture
def flush_cache(redis_client: Redis) -> Callable[[], Awaitable[None]]:
    async def inner() -> None:
        await redis_client.flushall()

    return inner


@pytest.fixture
def make_get_request(http_session: aiohttp.ClientSession) -> Callable[..., Awaitable[Response]]:
    """GET-запрос к API; путь — относительно /api/v1.

    Токен необязателен: без него запрос анонимный, как у обычного посетителя.
    """

    async def inner(path: str, params: dict[str, Any] | None = None, token: str | None = None) -> Response:
        url = f'{settings.service_url}{API_PREFIX}{path}'
        headers = {'Authorization': f'Bearer {token}'} if token else None
        async with http_session.get(url, params=params, headers=headers) as response:
            body = await response.json(content_type=None)
            return Response(status=response.status, body=body)

    return inner


# Сервис авторизации: тесты заводят в нём пользователей, чтобы проверить,
# кому API отдаёт подписочные фильмы. Учётные записи создаются один раз на
# сессию — регистрация и вход стоят Argon2, а между тестами они не меняются.

@pytest.fixture(scope='session')
def auth_request(http_session: aiohttp.ClientSession) -> Callable[..., Awaitable[Response]]:
    """Запрос к сервису авторизации; путь — относительно /auth/api/v1."""

    async def inner(
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> Response:
        url = f'{settings.auth_url}{AUTH_PREFIX}{path}'
        headers = {'Authorization': f'Bearer {token}'} if token else None
        async with http_session.request(method, url, json=json, headers=headers) as response:
            body = await response.json(content_type=None)
            return Response(status=response.status, body=body)

    return inner


@pytest.fixture(scope='session')
def register_user(auth_request: Callable[..., Awaitable[Response]]) -> Callable[[], Awaitable[tuple[str, str]]]:
    """Заводит нового пользователя и возвращает его идентификатор и access-токен."""

    async def inner() -> tuple[str, str]:
        login, password = f'user{uuid4().hex[:12]}', 'functional-tests-password'
        created = await auth_request('POST', '/signup', json={'login': login, 'password': password})
        if created.status != HTTPStatus.CREATED:
            raise RuntimeError(f'Не удалось зарегистрировать пользователя: {created.status} {created.body}')
        logged_in = await auth_request('POST', '/login', json={'login': login, 'password': password})
        if logged_in.status != HTTPStatus.OK:
            raise RuntimeError(f'Не удалось войти: {logged_in.status} {logged_in.body}')
        return created.body['id'], logged_in.body['access_token']

    return inner


@pytest.fixture(scope='session')
async def admin_token(auth_request: Callable[..., Awaitable[Response]]) -> str:
    """Токен суперпользователя: под ним назначаются роли."""
    response = await auth_request(
        'POST',
        '/login',
        json={'login': settings.auth_superuser_login, 'password': settings.auth_superuser_password},
    )
    if response.status != HTTPStatus.OK:
        raise RuntimeError(f'Не удалось войти суперпользователем: {response.status} {response.body}')
    return response.body['access_token']


@pytest.fixture(scope='session')
async def subscribers_role_id(auth_request: Callable[..., Awaitable[Response]], admin_token: str) -> str:
    """Идентификатор роли с правом films.subscription."""
    response = await auth_request('GET', '/roles', token=admin_token)
    roles = {role['name']: role['id'] for role in response.body}
    if settings.subscribers_role not in roles:
        raise RuntimeError(f'В сервисе авторизации нет роли {settings.subscribers_role}: {roles}')
    return roles[settings.subscribers_role]


@pytest.fixture(scope='session')
async def viewer_token(register_user: Callable[[], Awaitable[tuple[str, str]]]) -> str:
    """Токен пользователя без подписки: вошёл, но подписочные фильмы ему закрыты."""
    _, token = await register_user()
    return token


@pytest.fixture(scope='session')
async def subscriber_token(
    register_user: Callable[[], Awaitable[tuple[str, str]]],
    auth_request: Callable[..., Awaitable[Response]],
    admin_token: str,
    subscribers_role_id: str,
) -> str:
    """Токен пользователя с подпиской: ему назначена роль subscribers."""
    user_id, token = await register_user()
    assigned = await auth_request('PUT', f'/users/{user_id}/roles/{subscribers_role_id}', token=admin_token)
    if assigned.status != HTTPStatus.NO_CONTENT:
        raise RuntimeError(f'Не удалось выдать подписку: {assigned.status} {assigned.body}')
    return token
