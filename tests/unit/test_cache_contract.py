"""Контракт кеша: сбой хранилища кеша не прерывает запрос.

Реализации Cache сообщают о сбое только CacheUnavailableError, ModelCache
превращает его в промах и пропуск записи, а сервис отдаёт данные из
хранилища документов.
"""

from collections.abc import Sequence
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from models.film import Film
from services.base import Pagination
from services.cache import ModelCache
from services.film import FilmService
from storage.base import Document, DocumentStorage, SearchRequest
from storage.cache import Cache, CacheUnavailableError
from storage.redis import RedisCache


class BrokenCache(Cache):
    """Кеш, хранилище которого недоступно."""

    async def get(self, key: str) -> bytes | None:
        raise CacheUnavailableError('недоступен')

    async def set(self, key: str, value: bytes | str, expire: int) -> None:
        raise CacheUnavailableError('недоступен')


class MemoryCache(Cache):
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def set(self, key: str, value: bytes | str, expire: int) -> None:
        self.data[key] = value.encode() if isinstance(value, str) else value


class OneDocumentStorage(DocumentStorage):
    def __init__(self, doc: Document) -> None:
        self.doc = doc

    async def get(self, index: str, doc_id: str, fields: Sequence[str]) -> Document | None:
        return self.doc if doc_id == self.doc['id'] else None

    async def search(self, index: str, request: SearchRequest) -> list[Document]:
        return [self.doc]


@pytest.fixture
async def unreachable_redis():
    # На порту 1 никто не слушает; без повторов, чтобы тест не ждал.
    client = Redis(host='127.0.0.1', port=1, socket_connect_timeout=0.5, retry=Retry(NoBackoff(), retries=0))
    yield client
    await client.aclose()


# Реализации Cache

async def test_redis_cache_reports_failure_as_cache_unavailable(unreachable_redis):
    cache = RedisCache(unreachable_redis)

    with pytest.raises(CacheUnavailableError):
        await cache.get('key')
    with pytest.raises(CacheUnavailableError):
        await cache.set('key', b'value', expire=60)


# ModelCache

async def test_model_cache_miss_when_cache_unavailable():
    cache = ModelCache(BrokenCache(), expire=60)

    assert await cache.get('films:id:1', Film) is None


async def test_model_cache_skips_write_when_cache_unavailable():
    cache = ModelCache(BrokenCache(), expire=60)
    film = Film(id=uuid4(), title='The Star')

    await cache.set('films:id:1', film, Film)


async def test_model_cache_round_trip():
    cache = ModelCache(MemoryCache(), expire=60)
    film = Film(id=uuid4(), title='The Star')

    await cache.set('films:id:1', film, Film)

    assert await cache.get('films:id:1', Film) == film


async def test_model_cache_corrupted_record_is_miss():
    storage = MemoryCache()
    storage.data['films:id:1'] = b'{"broken": '

    assert await ModelCache(storage, expire=60).get('films:id:1', Film) is None


# Сервис поверх недоступного кеша

async def test_service_returns_document_when_cache_unavailable():
    doc = {'id': str(uuid4()), 'title': 'The Star', 'imdb_rating': 8.5}
    service = FilmService(OneDocumentStorage(doc), ModelCache(BrokenCache(), expire=60))

    film = await service.get_by_id(doc['id'])

    assert film is not None
    assert film.title == 'The Star'


async def test_service_search_works_when_cache_unavailable():
    doc = {'id': str(uuid4()), 'title': 'The Star', 'imdb_rating': 8.5}
    service = FilmService(OneDocumentStorage(doc), ModelCache(BrokenCache(), expire=60))

    films = await service.search('star', Pagination(page_number=1, page_size=50))

    assert [film.title for film in films] == ['The Star']
