"""Хранилища в Redis: сессии пользователей и кеш прав.

Ключи:

* `auth:session:<session_id>` — хеш сессии (user_id, refresh_jti), живёт
  столько же, сколько refresh-токен, и продлевается при каждом обновлении;
* `auth:user_sessions:<user_id>` — множество сессий пользователя, нужно
  для «выйти из остальных устройств»;
* `auth:access:<user_id>` — права пользователя (JSON) с отметкой версии;
* `auth:access_version` и `auth:access_version:<user_id>` — версии прав:
  общая растёт при изменении и удалении ролей, личная — при назначении и
  отзыве роли у пользователя;
* `auth:rate:<ключ>` — попытки входа и регистрации за скользящее окно:
  сортированное множество, оценка — время попытки в миллисекундах.

Redis сервиса авторизации — не кеш с вытеснением: без сессий пользователи
окажутся разлогинены, поэтому он настроен без вытеснения и с журналом AOF.
"""

import json
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from models.role import UserAccess
from models.session import Session
from storage.base import AccessCache, RateLimit, RateLimiter, RotateResult, SessionStore, StorageUnavailableError

SESSION_KEY = 'auth:session:{session_id}'
USER_SESSIONS_KEY = 'auth:user_sessions:{user_id}'
ACCESS_KEY = 'auth:access:{user_id}'
ACCESS_VERSION_KEY = 'auth:access_version'
USER_ACCESS_VERSION_KEY = 'auth:access_version:{user_id}'
RATE_KEY = 'auth:rate:{key}'

# Сравнить jti и заменить его нужно атомарно: иначе два одновременных запроса
# с одним refresh-токеном оба получили бы новую пару.
ROTATE_SCRIPT = """
local current = redis.call('HGET', KEYS[1], 'refresh_jti')
if not current then
    return 0
end
if current ~= ARGV[1] then
    return -1
end
redis.call('HSET', KEYS[1], 'refresh_jti', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return 1
"""
ROTATE_RESULTS = {1: RotateResult.ROTATED, -1: RotateResult.REUSED, 0: RotateResult.MISSING}

# Скользящее окно: в множестве лежат попытки за последние period. Проверить
# все лимиты и засчитать попытку нужно атомарно: иначе параллельные запросы
# прошли бы проверку одновременно и превысили лимит.
# KEYS — ключи лимитов; ARGV[1] — текущее время, мс; ARGV[2] — метка попытки;
# дальше по паре на ключ: лимит и окно, мс. Возвращает 0, если попытка
# засчитана, иначе — через сколько миллисекунд освободится место.
ACQUIRE_SCRIPT = """
local now = tonumber(ARGV[1])
local wait = 0
for i, key in ipairs(KEYS) do
    local limit = tonumber(ARGV[2 * i + 1])
    local window = tonumber(ARGV[2 * i + 2])
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
    if redis.call('ZCARD', key) >= limit then
        local oldest = tonumber(redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')[2])
        wait = math.max(wait, oldest + window - now)
    end
end
if wait > 0 then
    return wait
end
for i, key in ipairs(KEYS) do
    redis.call('ZADD', key, now, ARGV[2])
    redis.call('PEXPIRE', key, ARGV[2 * i + 2])
end
return 0
"""


@asynccontextmanager
async def redis_errors() -> AsyncIterator[None]:
    """Переводит ошибки Redis в ошибку хранилища."""
    try:
        yield
    except RedisError as exc:
        raise StorageUnavailableError(f'Redis: {exc}') from exc


def seconds(ttl: timedelta) -> int:
    return int(ttl.total_seconds())


class RedisSessionStore(SessionStore):
    def __init__(self, redis: Redis):
        self.redis = redis
        self._rotate = redis.register_script(ROTATE_SCRIPT)

    async def create(self, session: Session, ttl: timedelta) -> None:
        session_key = SESSION_KEY.format(session_id=session.id)
        user_key = USER_SESSIONS_KEY.format(user_id=session.user_id)
        async with redis_errors(), self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(session_key, mapping={'user_id': str(session.user_id), 'refresh_jti': session.refresh_jti})
            pipe.expire(session_key, seconds(ttl))
            pipe.sadd(user_key, str(session.id))
            # Множество живёт не меньше самой долгой сессии пользователя.
            pipe.expire(user_key, seconds(ttl))
            await pipe.execute()

    async def exists(self, session_id: UUID) -> bool:
        async with redis_errors():
            return bool(await self.redis.exists(SESSION_KEY.format(session_id=session_id)))

    async def rotate(
        self, user_id: UUID, session_id: UUID, old_jti: str, new_jti: str, ttl: timedelta,
    ) -> RotateResult:
        keys = [SESSION_KEY.format(session_id=session_id), USER_SESSIONS_KEY.format(user_id=user_id)]
        async with redis_errors():
            result = await self._rotate(keys=keys, args=[old_jti, new_jti, seconds(ttl)])
        return ROTATE_RESULTS[int(result)]

    async def delete(self, user_id: UUID, session_id: UUID) -> None:
        async with redis_errors(), self.redis.pipeline(transaction=True) as pipe:
            pipe.delete(SESSION_KEY.format(session_id=session_id))
            pipe.srem(USER_SESSIONS_KEY.format(user_id=user_id), str(session_id))
            await pipe.execute()

    async def delete_others(self, user_id: UUID, keep_session_id: UUID) -> int:
        user_key = USER_SESSIONS_KEY.format(user_id=user_id)
        async with redis_errors():
            members = await self.redis.smembers(user_key)
            others = [member.decode() for member in members if member.decode() != str(keep_session_id)]
            if not others:
                return 0
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.delete(*(SESSION_KEY.format(session_id=session_id) for session_id in others))
                pipe.srem(user_key, *others)
                deleted, _ = await pipe.execute()
        # В множестве могли остаться уже истёкшие сессии: считаем только живые.
        return deleted


class RedisAccessCache(AccessCache):
    def __init__(self, redis: Redis, user_version_ttl: timedelta):
        self.redis = redis
        # Личная версия должна пережить любую запись кеша, сделанную при ней:
        # иначе после её истечения счёт начнётся заново и совпадёт со старой записью.
        self.user_version_ttl = user_version_ttl

    async def get(self, user_id: UUID) -> tuple[UserAccess | None, str]:
        async with redis_errors():
            global_version, user_version, cached = await self.redis.mget(
                ACCESS_VERSION_KEY,
                USER_ACCESS_VERSION_KEY.format(user_id=user_id),
                ACCESS_KEY.format(user_id=user_id),
            )
        version = f'{int(global_version or 0)}:{int(user_version or 0)}'
        if cached is None:
            return None, version
        entry = json.loads(cached)
        if entry['version'] != version:
            return None, version
        return UserAccess.model_validate(entry['access']), version

    async def set(self, user_id: UUID, access: UserAccess, version: str, ttl: timedelta) -> None:
        entry = {'version': version, 'access': access.model_dump(mode='json')}
        async with redis_errors():
            await self.redis.set(ACCESS_KEY.format(user_id=user_id), json.dumps(entry), ex=seconds(ttl))

    async def invalidate_user(self, user_id: UUID) -> None:
        version_key = USER_ACCESS_VERSION_KEY.format(user_id=user_id)
        async with redis_errors(), self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(version_key)
            pipe.expire(version_key, seconds(self.user_version_ttl))
            pipe.delete(ACCESS_KEY.format(user_id=user_id))
            await pipe.execute()

    async def invalidate_all(self) -> None:
        async with redis_errors():
            await self.redis.incr(ACCESS_VERSION_KEY)


def milliseconds(period: timedelta) -> int:
    return int(period.total_seconds() * 1000)


class RedisRateLimiter(RateLimiter):
    def __init__(self, redis: Redis, clock: Callable[[], float] = time.time):
        self.redis = redis
        self._acquire = redis.register_script(ACQUIRE_SCRIPT)
        # Часы подменяются в тестах, чтобы проверить сдвиг окна без ожидания.
        self.clock = clock

    async def acquire(self, limits: Sequence[RateLimit]) -> timedelta | None:
        keys = [RATE_KEY.format(key=limit.key) for limit in limits]
        args: list[int | str] = [int(self.clock() * 1000), uuid4().hex]
        for limit in limits:
            args += [limit.limit, milliseconds(limit.period)]
        async with redis_errors():
            wait = int(await self._acquire(keys=keys, args=args))
        return timedelta(milliseconds=wait) if wait else None

    async def reset(self, key: str) -> None:
        async with redis_errors():
            await self.redis.delete(RATE_KEY.format(key=key))
