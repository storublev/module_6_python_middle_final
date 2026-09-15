"""Сервисы на хранилищах в памяти и быстрый хешер паролей."""

import os
from datetime import timedelta

import pytest
from pwdlib.hashers.argon2 import Argon2Hasher

from services.access import AccessService
from services.auth import AuthService, ClientInfo, RegistrationService
from services.passwords import PasswordHasher
from services.profile import ProfileService
from services.roles import RoleService
from services.tokens import TokenService
from tests.unit.fakes import (
    Database,
    FakeAccessCache,
    FakeLoginHistoryRepository,
    FakeRoleRepository,
    FakeSessionStore,
    FakeUserRepository,
)

SECRET_KEY = 'unit-test-secret-key-of-at-least-32-bytes'
# Настройки сервиса читаются при импорте cli: секретам нужны значения-заглушки.
os.environ.setdefault('AUTH_JWT_SECRET_KEY', SECRET_KEY)
os.environ.setdefault('AUTH_POSTGRES_PASSWORD', 'unit-tests')
PASSWORD = 'followtherabbit'
CLIENT = ClientInfo(user_agent='pytest', ip='127.0.0.1')


@pytest.fixture(scope='session')
def passwords() -> PasswordHasher:
    # Минимальные параметры Argon2: логика та же, а тесты не ждут настоящей стойкости.
    return PasswordHasher(Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1))


@pytest.fixture
def tokens() -> TokenService:
    return TokenService(SECRET_KEY, 'HS256', access_ttl=timedelta(minutes=15), refresh_ttl=timedelta(days=14))


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
def users(db: Database) -> FakeUserRepository:
    return FakeUserRepository(db)


@pytest.fixture
def roles_repo(db: Database) -> FakeRoleRepository:
    return FakeRoleRepository(db)


@pytest.fixture
def history(db: Database) -> FakeLoginHistoryRepository:
    return FakeLoginHistoryRepository(db)


@pytest.fixture
def sessions() -> FakeSessionStore:
    return FakeSessionStore()


@pytest.fixture
def cache() -> FakeAccessCache:
    return FakeAccessCache()


@pytest.fixture
def registration(users: FakeUserRepository, passwords: PasswordHasher) -> RegistrationService:
    return RegistrationService(users, passwords)


@pytest.fixture
def auth(
    users: FakeUserRepository,
    sessions: FakeSessionStore,
    history: FakeLoginHistoryRepository,
    tokens: TokenService,
    passwords: PasswordHasher,
) -> AuthService:
    return AuthService(users, sessions, history, tokens, passwords)


@pytest.fixture
def profiles(
    users: FakeUserRepository,
    roles_repo: FakeRoleRepository,
    history: FakeLoginHistoryRepository,
    sessions: FakeSessionStore,
    passwords: PasswordHasher,
) -> ProfileService:
    return ProfileService(users, roles_repo, history, sessions, passwords)


@pytest.fixture
def role_service(roles_repo: FakeRoleRepository, users: FakeUserRepository, cache: FakeAccessCache) -> RoleService:
    return RoleService(roles_repo, users, cache)


@pytest.fixture
def access(users: FakeUserRepository, cache: FakeAccessCache) -> AccessService:
    return AccessService(users, cache, cache_ttl=timedelta(minutes=10))
