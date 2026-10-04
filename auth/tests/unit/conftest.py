"""Сервисы на хранилищах в памяти и быстрый хешер паролей."""

import os
from datetime import timedelta

import pytest
from pwdlib.hashers.argon2 import Argon2Hasher

from services.access import AccessService
from services.access_invalidation import AccessInvalidator
from services.auth import AuthService, ClientInfo, RegistrationService, SignupService
from services.passwords import PasswordHasher
from services.profile import ProfileService
from services.roles import RoleService
from services.social import SocialAuthService
from services.throttling import Limit, Throttle, ThrottlingPolicy
from services.tokens import TokenService
from tests.unit.fakes import (
    Database,
    FakeAccessCache,
    FakeAccessInvalidationQueue,
    FakeLoginHistoryRepository,
    FakeOAuthStateStore,
    FakeProvider,
    FakeRateLimiter,
    FakeRoleRepository,
    FakeSessionStore,
    FakeSocialAccountRepository,
    FakeUserRepository,
)

SECRET_KEY = 'unit-test-secret-key-of-at-least-32-bytes'
# Настройки сервиса читаются при импорте cli: секретам нужны значения-заглушки.
os.environ.setdefault('AUTH_JWT_SECRET_KEY', SECRET_KEY)
os.environ.setdefault('AUTH_POSTGRES_PASSWORD', 'unit-tests')
PASSWORD = 'followtherabbit'
CLIENT = ClientInfo(user_agent='pytest', ip='127.0.0.1')
# Небольшие лимиты, чтобы тесты упирались в них за несколько попыток.
POLICY = ThrottlingPolicy(
    login_per_ip=Limit(attempts=5, period=timedelta(minutes=1)),
    login_per_account=Limit(attempts=3, period=timedelta(minutes=15)),
    signup_per_ip=Limit(attempts=2, period=timedelta(hours=1)),
    password_check_per_ip=Limit(attempts=4, period=timedelta(minutes=15)),
    password_check_per_account=Limit(attempts=2, period=timedelta(minutes=15)),
)


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
def limiter() -> FakeRateLimiter:
    return FakeRateLimiter()


@pytest.fixture
def throttle(limiter: FakeRateLimiter) -> Throttle:
    return Throttle(limiter, POLICY)


@pytest.fixture
def registration(users: FakeUserRepository, passwords: PasswordHasher) -> RegistrationService:
    return RegistrationService(users, passwords)


@pytest.fixture
def signups(registration: RegistrationService, throttle: Throttle) -> SignupService:
    return SignupService(registration, throttle)


@pytest.fixture
def auth(
    users: FakeUserRepository,
    sessions: FakeSessionStore,
    history: FakeLoginHistoryRepository,
    tokens: TokenService,
    passwords: PasswordHasher,
    throttle: Throttle,
) -> AuthService:
    return AuthService(users, sessions, history, tokens, passwords, throttle)


@pytest.fixture
def profiles(
    users: FakeUserRepository,
    roles_repo: FakeRoleRepository,
    history: FakeLoginHistoryRepository,
    sessions: FakeSessionStore,
    passwords: PasswordHasher,
    throttle: Throttle,
) -> ProfileService:
    return ProfileService(users, roles_repo, history, sessions, passwords, throttle)


@pytest.fixture
def invalidator(db: Database, cache: FakeAccessCache) -> AccessInvalidator:
    return AccessInvalidator(FakeAccessInvalidationQueue(db), cache)


@pytest.fixture
def role_service(
    roles_repo: FakeRoleRepository, users: FakeUserRepository, invalidator: AccessInvalidator,
) -> RoleService:
    return RoleService(roles_repo, users, invalidator)


@pytest.fixture
def access(users: FakeUserRepository, cache: FakeAccessCache) -> AccessService:
    return AccessService(users, cache, cache_ttl=timedelta(minutes=10))


@pytest.fixture
def social_accounts(db: Database) -> FakeSocialAccountRepository:
    return FakeSocialAccountRepository(db)


@pytest.fixture
def oauth_states() -> FakeOAuthStateStore:
    return FakeOAuthStateStore()


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def social(
    provider: FakeProvider,
    oauth_states: FakeOAuthStateStore,
    social_accounts: FakeSocialAccountRepository,
    users: FakeUserRepository,
    auth: AuthService,
) -> SocialAuthService:
    return SocialAuthService(
        {provider.name: provider}, oauth_states, social_accounts, users, auth, state_ttl=timedelta(minutes=10),
    )
