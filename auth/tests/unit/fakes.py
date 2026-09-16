"""Хранилища в памяти с тем же контрактом, что у PostgreSQL и Redis.

Сервисы зависят только от интерфейсов storage/base.py, поэтому бизнес-логику
можно проверить без баз данных.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import count
from typing import Any
from uuid import UUID, uuid4

from models.role import Role, UserAccess
from models.session import Session
from models.social import SocialAccount, SocialProfile
from models.user import LoginRecord, User
from storage.base import (
    AccessCache,
    AccessInvalidation,
    AccessInvalidationQueue,
    AlreadyExistsError,
    LoginHistoryRepository,
    OAuthProvider,
    OAuthStateStore,
    ProviderRejectedError,
    ProviderUnavailableError,
    RateLimit,
    RateLimiter,
    RoleRepository,
    RotateResult,
    SessionStore,
    SocialAccountRepository,
    StorageUnavailableError,
    UnlinkResult,
    UserRepository,
)
from storage.partitions import partition_name


def now() -> datetime:
    return datetime.now(UTC)


class Database:
    """Общие для репозиториев «таблицы»: роли пользователя видны и через пользователей, и через роли."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.roles: dict[UUID, Role] = {}
        self.user_roles: set[tuple[UUID, UUID]] = set()
        self.history: list[tuple[UUID, LoginRecord]] = []
        self.social_accounts: dict[UUID, SocialAccount] = {}
        self.partitions: set[date] = set()
        # Задания на сброс кеша прав: пишутся вместе с изменением ролей.
        self.invalidations: list[AccessInvalidation] = []
        self._invalidation_ids = count(1)

    def invalidate(self, user_id: UUID | None) -> None:
        self.invalidations.append(AccessInvalidation(id=next(self._invalidation_ids), user_id=user_id))


class FakeUserRepository(UserRepository):
    def __init__(self, db: Database):
        self.db = db
        self.access_reads = 0

    async def get(self, user_id: UUID) -> User | None:
        return self.db.users.get(user_id)

    async def get_by_login(self, login: str) -> User | None:
        return next((user for user in self.db.users.values() if user.login == login), None)

    async def create(self, login: str, password_hash: str, is_superuser: bool = False) -> User:
        if await self.get_by_login(login):
            raise AlreadyExistsError(login)
        user = User(id=uuid4(), login=login, password_hash=password_hash, credentials_version=0,
                    is_superuser=is_superuser, created_at=now())
        self.db.users[user.id] = user
        return user

    async def update_login(self, user_id: UUID, login: str) -> User:
        if await self.get_by_login(login):
            raise AlreadyExistsError(login)
        user = self.db.users[user_id].model_copy(update={'login': login})
        self.db.users[user_id] = user
        return user

    async def update_password(self, user_id: UUID, password_hash: str) -> int:
        user = self.db.users[user_id]
        version = user.credentials_version + 1
        self.db.users[user_id] = user.model_copy(
            update={'password_hash': password_hash, 'credentials_version': version},
        )
        return version

    async def get_credentials_version(self, user_id: UUID) -> int | None:
        user = self.db.users.get(user_id)
        return user.credentials_version if user else None

    async def get_access(self, user_id: UUID) -> UserAccess | None:
        self.access_reads += 1
        user = self.db.users.get(user_id)
        if user is None:
            return None
        roles = [self.db.roles[role_id] for owner, role_id in self.db.user_roles if owner == user_id]
        return UserAccess(
            is_superuser=user.is_superuser,
            roles=frozenset(role.name for role in roles),
            permissions=frozenset(permission for role in roles for permission in role.permissions),
        )


class FakeRoleRepository(RoleRepository):
    def __init__(self, db: Database):
        self.db = db

    async def get_all(self) -> list[Role]:
        return sorted(self.db.roles.values(), key=lambda role: role.name)

    async def get(self, role_id: UUID) -> Role | None:
        return self.db.roles.get(role_id)

    async def create(self, name: str, description: str | None, permissions: list[str]) -> Role:
        self._check_name(name)
        role = Role(id=uuid4(), name=name, description=description, permissions=tuple(permissions),
                    created_at=now(), updated_at=now())
        self.db.roles[role.id] = role
        return role

    async def update(self, role_id: UUID, changes: dict[str, Any]) -> Role | None:
        role = self.db.roles.get(role_id)
        if role is None:
            return None
        if 'name' in changes and changes['name'] != role.name:
            self._check_name(changes['name'])
        if 'permissions' in changes:
            changes = {**changes, 'permissions': tuple(changes['permissions'])}
        self.db.roles[role_id] = role.model_copy(update={**changes, 'updated_at': now()})
        if changes:
            self.db.invalidate(user_id=None)
        return self.db.roles[role_id]

    async def delete(self, role_id: UUID) -> bool:
        if self.db.roles.pop(role_id, None) is None:
            return False
        self.db.user_roles = {(user, role) for user, role in self.db.user_roles if role != role_id}
        self.db.invalidate(user_id=None)
        return True

    async def list_for_user(self, user_id: UUID) -> list[Role]:
        roles = [self.db.roles[role_id] for owner, role_id in self.db.user_roles if owner == user_id]
        return sorted(roles, key=lambda role: role.name)

    async def assign(self, user_id: UUID, role_id: UUID) -> None:
        self.db.user_roles.add((user_id, role_id))
        self.db.invalidate(user_id)

    async def revoke(self, user_id: UUID, role_id: UUID) -> bool:
        if (user_id, role_id) not in self.db.user_roles:
            return False
        self.db.user_roles.remove((user_id, role_id))
        self.db.invalidate(user_id)
        return True

    def _check_name(self, name: str) -> None:
        if any(role.name == name for role in self.db.roles.values()):
            raise AlreadyExistsError(name)


class FakeAccessInvalidationQueue(AccessInvalidationQueue):
    def __init__(self, db: Database):
        self.db = db

    async def process(
        self, handler: Callable[[list[AccessInvalidation]], Awaitable[None]], limit: int,
    ) -> int:
        tasks = self.db.invalidations[:limit]
        if not tasks:
            return 0
        await handler(tasks)
        self.db.invalidations = self.db.invalidations[len(tasks):]
        return len(tasks)


class FakeLoginHistoryRepository(LoginHistoryRepository):
    def __init__(self, db: Database):
        self.db = db

    async def add(self, user_id: UUID, user_agent: str | None, ip: str | None) -> None:
        self.db.history.append((user_id, LoginRecord(id=uuid4(), user_agent=user_agent, ip=ip, created_at=now())))

    async def get_page(self, user_id: UUID, offset: int, limit: int) -> list[LoginRecord]:
        records = [record for owner, record in reversed(self.db.history) if owner == user_id]
        return records[offset:offset + limit]

    async def ensure_partitions(self, months: Sequence[date]) -> list[str]:
        """Секции — свойство PostgreSQL; в памяти запоминаем только запрошенные месяцы."""
        created = [partition_name(month) for month in months if month not in self.db.partitions]
        self.db.partitions.update(months)
        return created


class FakeSessionStore(SessionStore):
    def __init__(self) -> None:
        self.sessions: dict[UUID, Session] = {}
        self.ttls: dict[UUID, timedelta] = {}
        # Имитация недоступного Redis для изменений, которые идут после записи в базу.
        self.writes_fail = False

    def _check_available(self) -> None:
        if self.writes_fail:
            raise StorageUnavailableError('Redis: connection refused')

    async def create(self, session: Session, ttl: timedelta) -> None:
        self.sessions[session.id] = session
        self.ttls[session.id] = ttl

    async def get(self, session_id: UUID) -> Session | None:
        return self.sessions.get(session_id)

    async def set_credentials_version(self, session_id: UUID, version: int) -> None:
        self._check_available()
        if session_id in self.sessions:
            self.sessions[session_id] = self.sessions[session_id].model_copy(update={'credentials_version': version})

    async def rotate(
        self, user_id: UUID, session_id: UUID, old_jti: str, new_jti: str, ttl: timedelta,
    ) -> RotateResult:
        session = self.sessions.get(session_id)
        if session is None:
            return RotateResult.MISSING
        if session.refresh_jti != old_jti:
            return RotateResult.REUSED
        self.sessions[session_id] = session.model_copy(update={'refresh_jti': new_jti})
        self.ttls[session_id] = ttl
        return RotateResult.ROTATED

    async def delete(self, user_id: UUID, session_id: UUID) -> None:
        self.sessions.pop(session_id, None)

    async def delete_others(self, user_id: UUID, keep_session_id: UUID) -> int:
        self._check_available()
        others = [
            sid for sid, session in self.sessions.items() if session.user_id == user_id and sid != keep_session_id
        ]
        for session_id in others:
            del self.sessions[session_id]
        return len(others)


class FakeAccessCache(AccessCache):
    """Кеш без версий: гонки проверяются на настоящей реализации в test_redis_storage."""

    def __init__(self) -> None:
        self.entries: dict[UUID, UserAccess] = {}
        # Имитация недоступного Redis при сбросе кеша.
        self.invalidation_fails = False

    async def get(self, user_id: UUID) -> tuple[UserAccess | None, str]:
        return self.entries.get(user_id), ''

    async def set(self, user_id: UUID, access: UserAccess, version: str, ttl: timedelta) -> None:
        self.entries[user_id] = access

    async def invalidate_user(self, user_id: UUID) -> None:
        self._check_available()
        self.entries.pop(user_id, None)

    async def invalidate_all(self) -> None:
        self._check_available()
        self.entries.clear()

    def _check_available(self) -> None:
        if self.invalidation_fails:
            raise StorageUnavailableError('Redis: connection refused')


class FakeRateLimiter(RateLimiter):
    """Счётчики без времени: окно не сдвигается, сдвиг проверяется на Redis в test_redis_storage."""

    def __init__(self) -> None:
        self.attempts: dict[str, int] = {}

    async def acquire(self, limits: Sequence[RateLimit]) -> timedelta | None:
        exhausted = [limit for limit in limits if self.attempts.get(limit.key, 0) >= limit.limit]
        if exhausted:
            return max(limit.period for limit in exhausted)
        for limit in limits:
            self.attempts[limit.key] = self.attempts.get(limit.key, 0) + 1
        return None

    async def reset(self, key: str) -> None:
        self.attempts.pop(key, None)


class FakeSocialAccountRepository(SocialAccountRepository):
    def __init__(self, db: Database):
        self.db = db

    async def get_user(self, provider: str, social_id: str) -> User | None:
        account = self._find(provider=provider, social_id=social_id)
        return self.db.users.get(account.user_id) if account else None

    async def create_user(self, login: str, provider: str, profile: SocialProfile) -> User:
        if any(user.login == login for user in self.db.users.values()):
            raise AlreadyExistsError(login)
        user = User(id=uuid4(), login=login, password_hash=None, credentials_version=0,
                    is_superuser=False, created_at=now())
        self.db.users[user.id] = user
        await self.link(user.id, provider, profile)
        return user

    async def link(self, user_id: UUID, provider: str, profile: SocialProfile) -> SocialAccount:
        if self._find(provider=provider, social_id=profile.social_id) or self._find(
            provider=provider, user_id=user_id,
        ):
            raise AlreadyExistsError(f'{provider}:{profile.social_id}')
        account = SocialAccount(
            id=uuid4(),
            user_id=user_id,
            provider=provider,
            social_id=profile.social_id,
            display_name=profile.display_name,
            email=profile.email,
            created_at=now(),
        )
        self.db.social_accounts[account.id] = account
        return account

    async def list_for_user(self, user_id: UUID) -> list[SocialAccount]:
        accounts = [a for a in self.db.social_accounts.values() if a.user_id == user_id]
        return sorted(accounts, key=lambda account: account.provider)

    async def unlink(self, user_id: UUID, provider: str) -> UnlinkResult:
        """Проверка и удаление вместе: в памяти одновременных запросов нет, очередь обеспечивает PostgreSQL."""
        user = self.db.users.get(user_id)
        account = self._find(provider=provider, user_id=user_id)
        if user is None or account is None:
            return UnlinkResult.NOT_LINKED
        linked = {a.provider for a in self.db.social_accounts.values() if a.user_id == user_id}
        if not user.has_password and linked == {provider}:
            return UnlinkResult.LAST_LOGIN_METHOD
        del self.db.social_accounts[account.id]
        return UnlinkResult.UNLINKED

    def _find(self, **fields: Any) -> SocialAccount | None:
        return next(
            (a for a in self.db.social_accounts.values()
             if all(getattr(a, key) == value for key, value in fields.items())),
            None,
        )


class FakeOAuthStateStore(OAuthStateStore):
    """Состояния в памяти. Срок жизни не моделируется: истечение проверяется удалением ключа."""

    def __init__(self) -> None:
        self.states: dict[str, str] = {}

    async def save(self, state: str, payload: str, ttl: timedelta) -> None:
        self.states[state] = payload

    async def pop(self, state: str) -> str | None:
        return self.states.pop(state, None)


class FakeProvider(OAuthProvider):
    """Поставщик, отвечающий заданным профилем или ошибкой."""

    def __init__(
        self,
        name: str = 'yandex',
        profile: SocialProfile | None = None,
        error: Exception | None = None,
    ) -> None:
        self._name = name
        self.profile = profile or SocialProfile(social_id='1', display_name='Neo', email='neo@example.com')
        self.error = error
        self.codes: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def title(self) -> str:
        return self._name.title()

    async def authorization_url(self, state: str, redirect_uri: str) -> str:
        return f'https://{self._name}.example.com/authorize?state={state}&redirect_uri={redirect_uri}'

    async def fetch_profile(self, code: str, redirect_uri: str) -> SocialProfile:
        self.codes.append(code)
        if self.error:
            raise self.error
        return self.profile


REJECTED = ProviderRejectedError('code is invalid')
UNAVAILABLE = ProviderUnavailableError('provider is down')
