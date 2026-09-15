"""Хранилища в Redis: сессии пользователей и кеш прав.

Ключи:

* `auth:session:<session_id>` — хеш сессии (user_id, refresh_jti,
  credentials_version), живёт столько же, сколько refresh-токен, и
  продлевается при каждом обновлении;
* `auth:user_session_expiry:<user_id>` — сессии пользователя для «выйти из
  остальных устройств»: сортированное множество, оценка — когда сессия
  истекает, мс. Истёкшие удаляются при каждом входе и обновлении токенов,
  а число сессий ограничено — множество не растёт у активного пользователя;
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

SESSION_KEY_PREFIX = 'auth:session:'
SESSION_KEY = SESSION_KEY_PREFIX + '{session_id}'
USER_SESSIONS_KEY = 'auth:user_session_expiry:{user_id}'
ACCESS_KEY = 'auth:access:{user_id}'
ACCESS_VERSION_KEY = 'auth:access_version'
USER_ACCESS_VERSION_KEY = 'auth:access_version:{user_id}'
RATE_KEY = 'auth:rate:{key}'

# Время во всех скриптах сессий передаётся строками в миллисекундах: так
# оценки не теряют точность при переводе чисел Lua в аргументы команд.
# Множество сессий пользователя живёт, пока жива самая долгая из них:
# его срок только продлевается, но не сокращается.
EXTEND_USER_SESSIONS = """
local function extend_user_sessions(key, ttl)
    if redis.call('PTTL', key) < tonumber(ttl) then
        redis.call('PEXPIRE', key, ttl)
    end
end
"""

# Сессия и её место в множестве сессий пользователя записываются вместе.
# Заодно из множества уходят истёкшие сессии, а если сессий больше предела —
# закрываются самые давно не продлевавшиеся (с наименьшим сроком), кроме новой.
# KEYS: сессия, множество сессий пользователя. ARGV: id сессии, user_id,
# refresh_jti, версия учётных данных, время жизни, срок окончания, текущее
# время, предел сессий, префикс ключа сессии. Возвращает, сколько закрыто.
CREATE_SCRIPT = EXTEND_USER_SESSIONS + """
redis.call('HSET', KEYS[1], 'user_id', ARGV[2], 'refresh_jti', ARGV[3], 'credentials_version', ARGV[4])
redis.call('PEXPIRE', KEYS[1], ARGV[5])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[7])
redis.call('ZADD', KEYS[2], ARGV[6], ARGV[1])
extend_user_sessions(KEYS[2], ARGV[5])
local excess = redis.call('ZCARD', KEYS[2]) - tonumber(ARGV[8])
if excess <= 0 then
    return 0
end
local evicted = {}
for _, session_id in ipairs(redis.call('ZRANGE', KEYS[2], 0, excess)) do
    if session_id ~= ARGV[1] and #evicted < excess then
        table.insert(evicted, session_id)
        redis.call('DEL', ARGV[9] .. session_id)
        redis.call('ZREM', KEYS[2], session_id)
    end
end
return #evicted
"""

# Сравнить jti и заменить его нужно атомарно: иначе два одновременных запроса
# с одним refresh-токеном оба получили бы новую пару. Продлённая сессия
# получает новый срок и в множестве сессий пользователя.
# KEYS: сессия, множество сессий пользователя. ARGV: старый jti, новый jti,
# время жизни, срок окончания, текущее время, id сессии.
ROTATE_SCRIPT = EXTEND_USER_SESSIONS + """
local current = redis.call('HGET', KEYS[1], 'refresh_jti')
if not current then
    return 0
end
if current ~= ARGV[1] then
    return -1
end
redis.call('HSET', KEYS[1], 'refresh_jti', ARGV[2])
redis.call('PEXPIRE', KEYS[1], ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[5])
redis.call('ZADD', KEYS[2], ARGV[4], ARGV[6])
extend_user_sessions(KEYS[2], ARGV[3])
return 1
"""
ROTATE_RESULTS = {1: RotateResult.ROTATED, -1: RotateResult.REUSED, 0: RotateResult.MISSING}

# HSET несуществующего ключа создал бы сессию без срока жизни и без
# refresh-токена: закрытую сессию обновлять нельзя.
SET_CREDENTIALS_VERSION_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    redis.call('HSET', KEYS[1], 'credentials_version', ARGV[1])
end
"""

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


def milliseconds(period: timedelta) -> int:
    return int(period.total_seconds() * 1000)


class RedisSessionStore(SessionStore):
    def __init__(self, redis: Redis, max_sessions: int, clock: Callable[[], float] = time.time):
        self.redis = redis
        # Сколько сессий (устройств) может быть у пользователя одновременно.
        self.max_sessions = max_sessions
        # Часы подменяются в тестах, чтобы проверить истечение сессий без ожидания.
        self.clock = clock
        self._create = redis.register_script(CREATE_SCRIPT)
        self._rotate = redis.register_script(ROTATE_SCRIPT)
        self._set_credentials_version = redis.register_script(SET_CREDENTIALS_VERSION_SCRIPT)

    def _now(self) -> int:
        return int(self.clock() * 1000)

    async def create(self, session: Session, ttl: timedelta) -> None:
        keys = [SESSION_KEY.format(session_id=session.id), USER_SESSIONS_KEY.format(user_id=session.user_id)]
        now = self._now()
        args = [
            str(session.id), str(session.user_id), session.refresh_jti, str(session.credentials_version),
            str(milliseconds(ttl)), str(now + milliseconds(ttl)), str(now), str(self.max_sessions),
            SESSION_KEY_PREFIX,
        ]
        async with redis_errors():
            await self._create(keys=keys, args=args)

    async def get(self, session_id: UUID) -> Session | None:
        async with redis_errors():
            fields = await self.redis.hgetall(SESSION_KEY.format(session_id=session_id))
        # Сессия без версии учётных данных открыта до её появления: такую не
        # принимаем, пользователь войдёт заново.
        if b'credentials_version' not in fields:
            return None
        return Session(
            id=session_id,
            user_id=UUID(fields[b'user_id'].decode()),
            refresh_jti=fields[b'refresh_jti'].decode(),
            credentials_version=int(fields[b'credentials_version']),
        )

    async def set_credentials_version(self, session_id: UUID, version: int) -> None:
        async with redis_errors():
            await self._set_credentials_version(keys=[SESSION_KEY.format(session_id=session_id)], args=[version])

    async def rotate(
        self, user_id: UUID, session_id: UUID, old_jti: str, new_jti: str, ttl: timedelta,
    ) -> RotateResult:
        keys = [SESSION_KEY.format(session_id=session_id), USER_SESSIONS_KEY.format(user_id=user_id)]
        now = self._now()
        args = [old_jti, new_jti, str(milliseconds(ttl)), str(now + milliseconds(ttl)), str(now), str(session_id)]
        async with redis_errors():
            result = await self._rotate(keys=keys, args=args)
        return ROTATE_RESULTS[int(result)]

    async def delete(self, user_id: UUID, session_id: UUID) -> None:
        async with redis_errors(), self.redis.pipeline(transaction=True) as pipe:
            pipe.delete(SESSION_KEY.format(session_id=session_id))
            pipe.zrem(USER_SESSIONS_KEY.format(user_id=user_id), str(session_id))
            await pipe.execute()

    async def delete_others(self, user_id: UUID, keep_session_id: UUID) -> int:
        user_key = USER_SESSIONS_KEY.format(user_id=user_id)
        now = self._now()
        async with redis_errors():
            # Только ещё не истёкшие: истёкших ключей сессий уже нет.
            members = await self.redis.zrangebyscore(user_key, f'({now}', '+inf')
            others = [member.decode() for member in members if member.decode() != str(keep_session_id)]
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(user_key, '-inf', now)
                if others:
                    pipe.delete(*(SESSION_KEY.format(session_id=session_id) for session_id in others))
                    pipe.zrem(user_key, *others)
                results = await pipe.execute()
        return results[1] if others else 0


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
