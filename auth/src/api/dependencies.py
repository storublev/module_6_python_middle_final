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
from services.auth import AuthService, RegistrationService
from services.passwords import PasswordHasher
from services.profile import ProfileService
from services.roles import RoleService
from services.tokens import TokenService
from storage.base import AccessCache, LoginHistoryRepository, RoleRepository, SessionStore, UserRepository
from storage.postgres import PostgresLoginHistoryRepository, PostgresRoleRepository, PostgresUserRepository
from storage.redis import RedisAccessCache, RedisSessionStore


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


DbSession = Annotated[AsyncSession, Depends(get_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]
Tokens = Annotated[TokenService, Depends(get_token_service)]
Passwords = Annotated[PasswordHasher, Depends(get_password_hasher)]


def get_user_repository(session: DbSession) -> UserRepository:
    return PostgresUserRepository(session)


def get_role_repository(session: DbSession) -> RoleRepository:
    return PostgresRoleRepository(session)


def get_history_repository(session: DbSession) -> LoginHistoryRepository:
    return PostgresLoginHistoryRepository(session)


def get_session_store(redis: RedisClient) -> SessionStore:
    return RedisSessionStore(redis)


def get_access_cache(redis: RedisClient) -> AccessCache:
    return RedisAccessCache(redis, user_version_ttl=2 * settings.access_cache_ttl)


Users = Annotated[UserRepository, Depends(get_user_repository)]
Roles = Annotated[RoleRepository, Depends(get_role_repository)]
History = Annotated[LoginHistoryRepository, Depends(get_history_repository)]
Sessions = Annotated[SessionStore, Depends(get_session_store)]
Cache = Annotated[AccessCache, Depends(get_access_cache)]


def get_registration_service(users: Users, passwords: Passwords) -> RegistrationService:
    return RegistrationService(users, passwords)


def get_auth_service(
    users: Users, sessions: Sessions, history: History, tokens: Tokens, passwords: Passwords,
) -> AuthService:
    return AuthService(users, sessions, history, tokens, passwords)


def get_profile_service(
    users: Users, roles: Roles, history: History, sessions: Sessions, passwords: Passwords,
) -> ProfileService:
    return ProfileService(users, roles, history, sessions, passwords)


def get_role_service(roles: Roles, users: Users, cache: Cache) -> RoleService:
    return RoleService(roles, users, cache)


def get_access_service(users: Users, cache: Cache) -> AccessService:
    return AccessService(users, cache, cache_ttl=settings.access_cache_ttl)


RegistrationServiceDep = Annotated[RegistrationService, Depends(get_registration_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]
RoleServiceDep = Annotated[RoleService, Depends(get_role_service)]
AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]
