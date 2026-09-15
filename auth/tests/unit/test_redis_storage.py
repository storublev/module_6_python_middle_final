"""Хранилища в Redis: сессии, одноразовость refresh-токена и версии кеша прав.

Redis заменён на fakeredis с Lua: скрипт замены refresh-токена выполняется
так же, как в настоящем Redis.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from models.role import UserAccess
from models.session import Session
from storage.base import RotateResult, StorageUnavailableError
from storage.redis import RedisAccessCache, RedisSessionStore

TTL = timedelta(days=14)


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis()
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def store(redis: FakeAsyncRedis) -> RedisSessionStore:
    return RedisSessionStore(redis)


@pytest.fixture
def cache(redis: FakeAsyncRedis) -> RedisAccessCache:
    return RedisAccessCache(redis, user_version_ttl=timedelta(minutes=20))


def new_session(user_id=None) -> Session:
    return Session(id=uuid4(), user_id=user_id or uuid4(), refresh_jti=uuid4().hex)


async def test_session_lives_as_long_as_refresh_token(store: RedisSessionStore, redis: FakeAsyncRedis) -> None:
    """Сессия хранится со временем жизни refresh-токена."""
    session = new_session()

    await store.create(session, TTL)

    assert await store.exists(session.id)
    assert await redis.ttl(f'auth:session:{session.id}') == TTL.total_seconds()


async def test_rotate_accepts_only_current_refresh_token(store: RedisSessionStore) -> None:
    """Сессия принимает только свой действующий refresh-токен, использованный — отвергает."""
    session = new_session()
    await store.create(session, TTL)

    assert await store.rotate(session.user_id, session.id, session.refresh_jti, 'next', TTL) is RotateResult.ROTATED
    assert await store.rotate(session.user_id, session.id, session.refresh_jti, 'other', TTL) is RotateResult.REUSED
    assert await store.rotate(session.user_id, session.id, 'next', 'third', TTL) is RotateResult.ROTATED


async def test_rotate_extends_session(store: RedisSessionStore, redis: FakeAsyncRedis) -> None:
    """Обновление токенов продлевает сессию: активный пользователь не разлогинивается."""
    session = new_session()
    await store.create(session, timedelta(minutes=1))

    await store.rotate(session.user_id, session.id, session.refresh_jti, 'next', TTL)

    assert await redis.ttl(f'auth:session:{session.id}') == TTL.total_seconds()


async def test_rotate_missing_session(store: RedisSessionStore) -> None:
    """Для закрытой или истёкшей сессии замена невозможна."""
    assert await store.rotate(uuid4(), uuid4(), 'jti', 'next', TTL) is RotateResult.MISSING


async def test_delete_session(store: RedisSessionStore) -> None:
    """Удалённая сессия больше не существует."""
    session = new_session()
    await store.create(session, TTL)

    await store.delete(session.user_id, session.id)

    assert not await store.exists(session.id)


async def test_delete_others_keeps_current_session(store: RedisSessionStore) -> None:
    """Закрываются все сессии пользователя, кроме текущей; чужие сессии не трогаются."""
    user_id = uuid4()
    current, other, another = new_session(user_id), new_session(user_id), new_session(user_id)
    stranger = new_session()
    for session in (current, other, another, stranger):
        await store.create(session, TTL)

    assert await store.delete_others(user_id, keep_session_id=current.id) == 2

    assert await store.exists(current.id)
    assert not await store.exists(other.id)
    assert not await store.exists(another.id)
    assert await store.exists(stranger.id)
    assert await store.delete_others(user_id, keep_session_id=current.id) == 0


async def test_access_cache_roundtrip(cache: RedisAccessCache) -> None:
    """Права, сохранённые с отметкой версии, читаются из кеша."""
    user_id = uuid4()
    access = UserAccess(roles=frozenset({'subscribers'}), permissions=frozenset({'films.subscription'}))

    cached, version = await cache.get(user_id)
    assert cached is None
    await cache.set(user_id, access, version, TTL)

    assert (await cache.get(user_id))[0] == access


@pytest.mark.parametrize('invalidate', ['user', 'all'])
async def test_stale_write_after_invalidation_is_ignored(cache: RedisAccessCache, invalidate: str) -> None:
    """Права, прочитанные до назначения роли, но записанные после, из кеша не выдаются.

    Так бывает при гонке: проверка прочитала из базы старые права, в это время
    назначили роль и сбросили кеш, и только потом проверка записала старые права.
    """
    user_id = uuid4()
    _, version = await cache.get(user_id)

    if invalidate == 'user':
        await cache.invalidate_user(user_id)
    else:
        await cache.invalidate_all()
    await cache.set(user_id, UserAccess(), version, TTL)

    assert (await cache.get(user_id))[0] is None


async def test_invalidate_user_touches_only_this_user(cache: RedisAccessCache) -> None:
    """Сброс прав одного пользователя не сбрасывает кеш остальных."""
    first, second = uuid4(), uuid4()
    for user_id in (first, second):
        _, version = await cache.get(user_id)
        await cache.set(user_id, UserAccess(), version, TTL)

    await cache.invalidate_user(first)

    assert (await cache.get(first))[0] is None
    assert (await cache.get(second))[0] == UserAccess()


async def test_redis_errors_become_storage_unavailable(redis: FakeAsyncRedis, monkeypatch) -> None:
    """Сбой Redis выходит из хранилищ как StorageUnavailableError, а не ошибка redis-py."""
    async def broken(*args, **kwargs):
        raise RedisConnectionError('connection refused')

    monkeypatch.setattr(redis, 'exists', broken)
    monkeypatch.setattr(redis, 'mget', broken)

    with pytest.raises(StorageUnavailableError):
        await RedisSessionStore(redis).exists(uuid4())
    with pytest.raises(StorageUnavailableError):
        await RedisAccessCache(redis, TTL).get(uuid4())
