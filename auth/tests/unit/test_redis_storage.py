"""Хранилища в Redis: сессии, одноразовость refresh-токена, версии кеша прав и лимиты попыток.

Redis заменён на fakeredis с Lua: скрипты замены refresh-токена и учёта
попыток выполняются так же, как в настоящем Redis.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from models.role import UserAccess
from models.session import Session
from storage.base import RateLimit, RotateResult, StorageUnavailableError
from storage.redis import RedisAccessCache, RedisRateLimiter, RedisSessionStore

TTL = timedelta(days=14)
MAX_SESSIONS = 3


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis()
    yield client
    await client.flushall()
    await client.aclose()


class Clock:
    """Часы, которые тест переводит вперёд вместо ожидания."""

    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(redis: FakeAsyncRedis, clock: Clock) -> RedisSessionStore:
    return RedisSessionStore(redis, max_sessions=MAX_SESSIONS, clock=clock)


@pytest.fixture
def cache(redis: FakeAsyncRedis) -> RedisAccessCache:
    return RedisAccessCache(redis, user_version_ttl=timedelta(minutes=20))


def new_session(user_id=None) -> Session:
    return Session(id=uuid4(), user_id=user_id or uuid4(), refresh_jti=uuid4().hex, credentials_version=3)


async def exists(store: RedisSessionStore, session: Session) -> bool:
    return await store.get(session.id) is not None


async def test_session_lives_as_long_as_refresh_token(store: RedisSessionStore, redis: FakeAsyncRedis) -> None:
    """Сессия хранится со временем жизни refresh-токена и читается со всеми полями."""
    session = new_session()

    await store.create(session, TTL)

    assert await store.get(session.id) == session
    assert await redis.ttl(f'auth:session:{session.id}') == TTL.total_seconds()


async def test_set_credentials_version(store: RedisSessionStore) -> None:
    """Живая сессия переводится на новую версию учётных данных, срок её жизни не меняется."""
    session = new_session()
    await store.create(session, TTL)

    await store.set_credentials_version(session.id, 4)

    assert (await store.get(session.id)).credentials_version == 4


async def test_set_credentials_version_does_not_revive_session(store: RedisSessionStore, redis: FakeAsyncRedis) -> None:
    """Для закрытой сессии смена версии ничего не создаёт: иначе появилась бы сессия без срока жизни."""
    session_id = uuid4()

    await store.set_credentials_version(session_id, 4)

    assert not await redis.exists(f'auth:session:{session_id}')


async def test_session_without_credentials_version_is_not_accepted(
    store: RedisSessionStore, redis: FakeAsyncRedis,
) -> None:
    """Сессия, открытая до появления версии учётных данных, не принимается — нужно войти заново."""
    session_id, user_id = uuid4(), uuid4()
    await redis.hset(f'auth:session:{session_id}', mapping={'user_id': str(user_id), 'refresh_jti': 'jti'})

    assert await store.get(session_id) is None


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

    assert not await exists(store, session)


async def test_delete_others_keeps_current_session(store: RedisSessionStore) -> None:
    """Закрываются все сессии пользователя, кроме текущей; чужие сессии не трогаются."""
    user_id = uuid4()
    current, other, another = new_session(user_id), new_session(user_id), new_session(user_id)
    stranger = new_session()
    for session in (current, other, another, stranger):
        await store.create(session, TTL)

    assert await store.delete_others(user_id, keep_session_id=current.id) == 2

    assert await exists(store, current)
    assert not await exists(store, other)
    assert not await exists(store, another)
    assert await exists(store, stranger)
    assert await store.delete_others(user_id, keep_session_id=current.id) == 0


async def user_sessions(redis: FakeAsyncRedis, user_id) -> list[str]:
    return [member.decode() for member in await redis.zrange(f'auth:user_session_expiry:{user_id}', 0, -1)]


async def test_expired_sessions_leave_user_index(store: RedisSessionStore, redis: FakeAsyncRedis, clock: Clock) -> None:
    """Истёкшая сессия уходит из множества сессий пользователя при следующем входе: оно не растёт."""
    user_id = uuid4()
    short, long = new_session(user_id), new_session(user_id)
    await store.create(short, timedelta(minutes=1))
    await store.create(long, TTL)

    clock.now += 120
    fresh = new_session(user_id)
    await store.create(fresh, TTL)

    assert sorted(await user_sessions(redis, user_id)) == sorted([str(long.id), str(fresh.id)])


async def test_rotate_extends_session_in_user_index(
    store: RedisSessionStore, redis: FakeAsyncRedis, clock: Clock,
) -> None:
    """Продлённая сессия получает новый срок и в множестве сессий пользователя — её не вычистят раньше времени."""
    session = new_session()
    await store.create(session, timedelta(minutes=1))

    clock.now += 30
    await store.rotate(session.user_id, session.id, session.refresh_jti, 'next', TTL)

    score = await redis.zscore(f'auth:user_session_expiry:{session.user_id}', str(session.id))
    assert score == (clock.now + TTL.total_seconds()) * 1000


async def test_user_index_lives_as_long_as_longest_session(store: RedisSessionStore, redis: FakeAsyncRedis) -> None:
    """Срок множества сессий пользователя продлевается, но не сокращается короткой сессией."""
    user_id = uuid4()
    await store.create(new_session(user_id), TTL)
    await store.create(new_session(user_id), timedelta(minutes=1))

    assert await redis.ttl(f'auth:user_session_expiry:{user_id}') == TTL.total_seconds()


async def test_session_limit_closes_least_recently_extended(
    store: RedisSessionStore, redis: FakeAsyncRedis, clock: Clock,
) -> None:
    """Сверх предела сессий закрывается та, что дольше всех не продлевалась; продлённая остаётся."""
    user_id = uuid4()
    sessions = [new_session(user_id) for _ in range(MAX_SESSIONS)]
    for session in sessions:
        await store.create(session, TTL)
        clock.now += 1
    await store.rotate(user_id, sessions[0].id, sessions[0].refresh_jti, 'next', TTL)

    newest = new_session(user_id)
    await store.create(newest, TTL)

    assert not await exists(store, sessions[1])
    for session in (sessions[0], sessions[2], newest):
        assert await exists(store, session)
    assert len(await user_sessions(redis, user_id)) == MAX_SESSIONS


async def test_session_limit_never_closes_new_session(store: RedisSessionStore, redis: FakeAsyncRedis) -> None:
    """Даже при совпадающих сроках новая сессия не закрывается пределом — закрываются прежние."""
    user_id = uuid4()
    sessions = [new_session(user_id) for _ in range(MAX_SESSIONS + 2)]
    for session in sessions:
        await store.create(session, TTL)
        assert await exists(store, session)

    assert len(await user_sessions(redis, user_id)) == MAX_SESSIONS


async def test_delete_others_counts_only_live_sessions(store: RedisSessionStore, clock: Clock) -> None:
    """Истёкшие сессии «выйти из остальных» не считает и убирает из множества."""
    user_id = uuid4()
    current, expired_session, other = new_session(user_id), new_session(user_id), new_session(user_id)
    await store.create(expired_session, timedelta(minutes=1))
    await store.create(current, TTL)
    await store.create(other, TTL)

    clock.now += 120

    assert await store.delete_others(user_id, keep_session_id=current.id) == 1
    assert await exists(store, current)
    assert not await exists(store, other)


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

    monkeypatch.setattr(redis, 'hgetall', broken)
    monkeypatch.setattr(redis, 'mget', broken)

    with pytest.raises(StorageUnavailableError):
        await RedisSessionStore(redis, max_sessions=MAX_SESSIONS).get(uuid4())
    with pytest.raises(StorageUnavailableError):
        await RedisAccessCache(redis, TTL).get(uuid4())


@pytest.fixture
def limiter(redis: FakeAsyncRedis, clock: Clock) -> RedisRateLimiter:
    return RedisRateLimiter(redis, clock=clock)


MINUTE = timedelta(minutes=1)


async def test_rate_limit_allows_limit_attempts(limiter: RedisRateLimiter, clock: Clock) -> None:
    """В окне засчитывается не больше limit попыток; ответ — через сколько освободится место."""
    limit = RateLimit('login:ip:10.0.0.1', limit=3, period=MINUTE)
    for _ in range(3):
        assert await limiter.acquire([limit]) is None
        clock.now += 10

    assert await limiter.acquire([limit]) == MINUTE - timedelta(seconds=30)


async def test_rate_limit_window_slides(limiter: RedisRateLimiter, clock: Clock) -> None:
    """Окно скользящее: место освобождается, когда самая старая попытка выходит из окна, а не с новой минутой."""
    limit = RateLimit('login:ip:10.0.0.1', limit=2, period=MINUTE)
    await limiter.acquire([limit])
    clock.now += 30
    await limiter.acquire([limit])

    clock.now += 29
    assert await limiter.acquire([limit]) is not None
    clock.now += 2
    assert await limiter.acquire([limit]) is None
    assert await limiter.acquire([limit]) is not None


async def test_rejected_attempt_is_not_counted_anywhere(limiter: RedisRateLimiter) -> None:
    """Если исчерпан один лимит, попытка не засчитывается и в остальные: другой адрес этим логином не заблокировать."""
    ip = RateLimit('login:ip:10.0.0.1', limit=1, period=MINUTE)
    account = RateLimit('login:account:neo', limit=2, period=MINUTE)
    assert await limiter.acquire([ip, account]) is None

    for _ in range(5):
        assert await limiter.acquire([ip, account]) is not None

    assert await limiter.acquire([RateLimit('login:ip:10.0.0.2', limit=1, period=MINUTE), account]) is None


async def test_rate_limit_keys_expire_with_window(limiter: RedisRateLimiter, redis: FakeAsyncRedis) -> None:
    """Ключ попыток живёт не дольше окна: неактивные адреса и логины не копятся в Redis."""
    await limiter.acquire([RateLimit('signup:ip:10.0.0.1', limit=5, period=MINUTE)])

    assert 0 < await redis.pttl('auth:rate:signup:ip:10.0.0.1') <= MINUTE.total_seconds() * 1000


async def test_rate_limit_reset(limiter: RedisRateLimiter) -> None:
    """Обнулённый счётчик снова пропускает попытки."""
    limit = RateLimit('login:account:neo', limit=1, period=MINUTE)
    await limiter.acquire([limit])

    await limiter.reset('login:account:neo')

    assert await limiter.acquire([limit]) is None
