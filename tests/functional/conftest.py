"""Общие фикстуры функциональных тестов.

Клиенты Elasticsearch, Redis и HTTP создаются один раз на сессию: установка
соединений — самая дорогая часть подготовки. Индексы тоже создаются один раз,
а перед каждым тестом из них удаляются документы и сбрасывается кеш, чтобы
тесты не видели чужих данных.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any

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
    """GET-запрос к API; путь — относительно /api/v1."""

    async def inner(path: str, params: dict[str, Any] | None = None) -> Response:
        url = f'{settings.service_url}{API_PREFIX}{path}'
        async with http_session.get(url, params=params) as response:
            body = await response.json(content_type=None)
            return Response(status=response.status, body=body)

    return inner
