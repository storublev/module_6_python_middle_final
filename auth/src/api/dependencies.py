"""Сборка сервисов для эндпоинтов (Composition Root).

Только здесь выбираются конкретные реализации хранилищ: PostgreSQL и Redis.
Сервисы получают их через конструктор и зависят от интерфейсов из
storage/base.py, поэтому ничего не знают ни о SQLAlchemy и Redis, ни о FastAPI.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from db.postgres import get_session
from db.redis import get_redis
from services.access import AccessService
from services.access_invalidation import AccessInvalidator
from services.auth import AuthService, RegistrationService, SignupService
from services.passwords import PasswordHasher
from services.profile import ProfileService
from services.roles import RoleService
from services.throttling import Limit, Throttle, ThrottlingPolicy
from services.tokens import TokenService
from storage.base import (
    AccessCache,
    AccessInvalidationQueue,
    LoginHistoryRepository,
    RateLimiter,
    RoleRepository,
    SessionStore,
    UserRepository,
)
from storage.postgres import (
    PostgresAccessInvalidationQueue,
    PostgresLoginHistoryRepository,
    PostgresRoleRepository,
    PostgresUserRepository,
)
from storage.redis import RedisAccessCache, RedisRateLimiter, RedisSessionStore


@lru_cache
def get_token_service() -> TokenService:
    return TokenService(
        secret_key=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        access_ttl=settings.access_token_ttl,
        refresh_ttl=settings.refresh_token_ttl,
    )


@lru_cache
def get_password_hasher() -> PasswordHasher:
    # Хешер создаётся один раз: при создании он считает хеш-заглушку.
    return PasswordHasher()


@lru_cache
def get_throttling_policy() -> ThrottlingPolicy:
    return ThrottlingPolicy(
        login_per_ip=Limit(settings.login_attempts_per_ip, settings.login_attempts_per_ip_period),
        login_per_account=Limit(settings.login_attempts_per_account, settings.login_attempts_per_account_period),
        signup_per_ip=Limit(settings.signup_attempts_per_ip, settings.signup_attempts_per_ip_period),
    )


DbSession = Annotated[AsyncSession, Depends(get_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]
Tokens = Annotated[TokenService, Depends(get_token_service)]
Passwords = Annotated[PasswordHasher, Depends(get_password_hasher)]
Policy = Annotated[ThrottlingPolicy, Depends(get_throttling_policy)]


def get_user_repository(session: DbSession) -> UserRepository:
    return PostgresUserRepository(session)


def get_role_repository(session: DbSession) -> RoleRepository:
    return PostgresRoleRepository(session)


def get_history_repository(session: DbSession) -> LoginHistoryRepository:
    return PostgresLoginHistoryRepository(session)


def get_invalidation_queue(session: DbSession) -> AccessInvalidationQueue:
    return PostgresAccessInvalidationQueue(session)


def get_session_store(redis: RedisClient) -> SessionStore:
    return RedisSessionStore(redis)


def get_access_cache(redis: RedisClient) -> AccessCache:
    return RedisAccessCache(redis, user_version_ttl=2 * settings.access_cache_ttl)


def get_rate_limiter(redis: RedisClient) -> RateLimiter:
    return RedisRateLimiter(redis)


Users = Annotated[UserRepository, Depends(get_user_repository)]
Roles = Annotated[RoleRepository, Depends(get_role_repository)]
History = Annotated[LoginHistoryRepository, Depends(get_history_repository)]
Sessions = Annotated[SessionStore, Depends(get_session_store)]
Cache = Annotated[AccessCache, Depends(get_access_cache)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
Invalidations = Annotated[AccessInvalidationQueue, Depends(get_invalidation_queue)]


def get_access_invalidator(queue: Invalidations, cache: Cache) -> AccessInvalidator:
    # Вызывается и вне запросов — фоновым повтором сброса кеша в main.py.
    return AccessInvalidator(queue, cache)


Invalidator = Annotated[AccessInvalidator, Depends(get_access_invalidator)]


def get_throttle(limiter: Limiter, policy: Policy) -> Throttle:
    return Throttle(limiter, policy)


ThrottleDep = Annotated[Throttle, Depends(get_throttle)]


def get_signup_service(users: Users, passwords: Passwords, throttle: ThrottleDep) -> SignupService:
    return SignupService(RegistrationService(users, passwords), throttle)


def get_auth_service(
    users: Users, sessions: Sessions, history: History, tokens: Tokens, passwords: Passwords, throttle: ThrottleDep,
) -> AuthService:
    return AuthService(users, sessions, history, tokens, passwords, throttle)


def get_profile_service(
    users: Users, roles: Roles, history: History, sessions: Sessions, passwords: Passwords,
) -> ProfileService:
    return ProfileService(users, roles, history, sessions, passwords)


def get_role_service(roles: Roles, users: Users, invalidator: Invalidator) -> RoleService:
    return RoleService(roles, users, invalidator)


def get_access_service(users: Users, cache: Cache) -> AccessService:
    return AccessService(users, cache, cache_ttl=settings.access_cache_ttl)


SignupServiceDep = Annotated[SignupService, Depends(get_signup_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]
RoleServiceDep = Annotated[RoleService, Depends(get_role_service)]
AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]
