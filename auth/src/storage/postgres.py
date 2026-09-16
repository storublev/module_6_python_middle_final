"""Хранилища на PostgreSQL (SQLAlchemy, asyncpg).

Каждый метод — законченная операция: изменения фиксируются в нём же.
Сессия SQLAlchemy одна на запрос, её создаёт и закрывает FastAPI.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.role import Role, UserAccess
from models.social import SocialAccount, SocialProfile
from models.user import LoginRecord, User
from storage.base import (
    AccessInvalidation,
    AccessInvalidationQueue,
    AlreadyExistsError,
    LoginHistoryRepository,
    RoleRepository,
    SocialAccountRepository,
    StorageUnavailableError,
    UnlinkResult,
    UserRepository,
)
from storage.orm import (
    SCHEMA,
    AccessInvalidationRow,
    LoginHistoryRow,
    RoleRow,
    SocialAccountRow,
    UserRoleRow,
    UserRow,
)
from storage.partitions import TABLE, partition_bounds, partition_name

# Сбои соединения: asyncpg поднимает OSError, если сервер не принимает
# соединения, SQLAlchemy — OperationalError и InterfaceError, если оно оборвалось.
CONNECTION_ERRORS = (OperationalError, InterfaceError, OSError, TimeoutError)
UNIQUE_VIOLATION = '23505'


class PostgresRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @asynccontextmanager
    async def _errors(self) -> AsyncIterator[None]:
        """Переводит ошибки SQLAlchemy и asyncpg в ошибки хранилища."""
        try:
            yield
        except IntegrityError as exc:
            await self.session.rollback()
            if getattr(exc.orig, 'sqlstate', None) == UNIQUE_VIOLATION:
                raise AlreadyExistsError(str(exc.orig)) from exc
            raise
        except CONNECTION_ERRORS as exc:
            raise StorageUnavailableError(f'PostgreSQL: {exc}') from exc


class PostgresUserRepository(PostgresRepository, UserRepository):
    async def get(self, user_id: UUID) -> User | None:
        async with self._errors():
            row = await self.session.get(UserRow, user_id)
        return User.model_validate(row) if row else None

    async def get_by_login(self, login: str) -> User | None:
        async with self._errors():
            row = await self.session.scalar(select(UserRow).where(UserRow.login == login))
        return User.model_validate(row) if row else None

    async def create(self, login: str, password_hash: str, is_superuser: bool = False) -> User:
        async with self._errors():
            row = await self.session.scalar(
                insert(UserRow)
                .values(login=login, password_hash=password_hash, is_superuser=is_superuser)
                .returning(UserRow),
            )
            await self.session.commit()
        return User.model_validate(row)

    async def update_login(self, user_id: UUID, login: str) -> User:
        async with self._errors():
            row = await self.session.scalar(
                update(UserRow).where(UserRow.id == user_id).values(login=login).returning(UserRow),
            )
            await self.session.commit()
        return User.model_validate(row)

    async def update_password(self, user_id: UUID, password_hash: str) -> int:
        # Пароль и версия учётных данных меняются одним UPDATE: либо оба, либо ничего.
        query = (
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(password_hash=password_hash, credentials_version=UserRow.credentials_version + 1)
            .returning(UserRow.credentials_version)
        )
        async with self._errors():
            version = await self.session.scalar(query)
            await self.session.commit()
        return version

    async def get_credentials_version(self, user_id: UUID) -> int | None:
        async with self._errors():
            return await self.session.scalar(select(UserRow.credentials_version).where(UserRow.id == user_id))

    async def get_access(self, user_id: UUID) -> UserAccess | None:
        query = (
            select(UserRow.is_superuser, RoleRow.name, RoleRow.permissions)
            .outerjoin(UserRoleRow, UserRoleRow.user_id == UserRow.id)
            .outerjoin(RoleRow, RoleRow.id == UserRoleRow.role_id)
            .where(UserRow.id == user_id)
        )
        async with self._errors():
            rows = (await self.session.execute(query)).all()
        if not rows:
            return None
        # Без ролей LEFT JOIN вернёт одну строку с NULL вместо роли.
        roles = [(name, permissions) for _, name, permissions in rows if name is not None]
        return UserAccess(
            is_superuser=rows[0].is_superuser,
            roles=frozenset(name for name, _ in roles),
            permissions=frozenset(permission for _, permissions in roles for permission in permissions),
        )


class PostgresRoleRepository(PostgresRepository, RoleRepository):
    async def get_all(self) -> list[Role]:
        async with self._errors():
            rows = await self.session.scalars(select(RoleRow).order_by(RoleRow.name))
        return [Role.model_validate(row) for row in rows]

    async def get(self, role_id: UUID) -> Role | None:
        async with self._errors():
            row = await self.session.get(RoleRow, role_id)
        return Role.model_validate(row) if row else None

    async def create(self, name: str, description: str | None, permissions: list[str]) -> Role:
        async with self._errors():
            row = await self.session.scalar(
                insert(RoleRow)
                .values(name=name, description=description, permissions=permissions)
                .returning(RoleRow),
            )
            await self.session.commit()
        return Role.model_validate(row)

    async def update(self, role_id: UUID, changes: dict[str, Any]) -> Role | None:
        if not changes:
            return await self.get(role_id)
        async with self._errors():
            row = await self.session.scalar(
                update(RoleRow).where(RoleRow.id == role_id).values(**changes).returning(RoleRow),
            )
            if row is not None:
                await self._invalidate(user_id=None)
            await self.session.commit()
        return Role.model_validate(row) if row else None

    async def delete(self, role_id: UUID) -> bool:
        # Назначения роли удаляет внешний ключ ON DELETE CASCADE.
        async with self._errors():
            deleted = await self.session.scalar(delete(RoleRow).where(RoleRow.id == role_id).returning(RoleRow.id))
            if deleted is not None:
                await self._invalidate(user_id=None)
            await self.session.commit()
        return deleted is not None

    async def list_for_user(self, user_id: UUID) -> list[Role]:
        query = (
            select(RoleRow)
            .join(UserRoleRow, UserRoleRow.role_id == RoleRow.id)
            .where(UserRoleRow.user_id == user_id)
            .order_by(RoleRow.name)
        )
        async with self._errors():
            rows = await self.session.scalars(query)
        return [Role.model_validate(row) for row in rows]

    async def assign(self, user_id: UUID, role_id: UUID) -> None:
        async with self._errors():
            await self.session.execute(
                pg_insert(UserRoleRow).values(user_id=user_id, role_id=role_id).on_conflict_do_nothing(),
            )
            await self._invalidate(user_id)
            await self.session.commit()

    async def revoke(self, user_id: UUID, role_id: UUID) -> bool:
        query = (
            delete(UserRoleRow)
            .where(UserRoleRow.user_id == user_id, UserRoleRow.role_id == role_id)
            .returning(UserRoleRow.role_id)
        )
        async with self._errors():
            deleted = await self.session.scalar(query)
            if deleted is not None:
                await self._invalidate(user_id)
            await self.session.commit()
        return deleted is not None

    async def _invalidate(self, user_id: UUID | None) -> None:
        """Записывает задание на сброс кеша прав в текущую транзакцию."""
        await self.session.execute(insert(AccessInvalidationRow).values(user_id=user_id))


class PostgresAccessInvalidationQueue(PostgresRepository, AccessInvalidationQueue):
    async def process(
        self, handler: Callable[[list[AccessInvalidation]], Awaitable[None]], limit: int,
    ) -> int:
        # Задания блокируются до конца транзакции: другой процесс сервиса их
        # пропустит (SKIP LOCKED) и не будет сбрасывать кеш дважды.
        query = (
            select(AccessInvalidationRow)
            .order_by(AccessInvalidationRow.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        async with self._errors():
            rows = (await self.session.scalars(query)).all()
            tasks = [AccessInvalidation(id=row.id, user_id=row.user_id) for row in rows]
            if not tasks:
                await self.session.rollback()
                return 0
            try:
                await handler(tasks)
            except BaseException:
                await self.session.rollback()
                raise
            ids = [task.id for task in tasks]
            await self.session.execute(delete(AccessInvalidationRow).where(AccessInvalidationRow.id.in_(ids)))
            await self.session.commit()
        return len(tasks)


class PostgresLoginHistoryRepository(PostgresRepository, LoginHistoryRepository):
    async def add(self, user_id: UUID, user_agent: str | None, ip: str | None) -> None:
        async with self._errors():
            await self.session.execute(insert(LoginHistoryRow).values(user_id=user_id, user_agent=user_agent, ip=ip))
            await self.session.commit()

    async def get_page(self, user_id: UUID, offset: int, limit: int) -> list[LoginRecord]:
        query = (
            select(LoginHistoryRow)
            .where(LoginHistoryRow.user_id == user_id)
            .order_by(LoginHistoryRow.created_at.desc(), LoginHistoryRow.id.desc())
            .offset(offset)
            .limit(limit)
        )
        async with self._errors():
            rows = await self.session.scalars(query)
        return [LoginRecord.model_validate(row) for row in rows]

    async def ensure_partitions(self, months: Sequence[date]) -> list[str]:
        """Создаёт недостающие месячные секции истории входов.

        Имя секции нигде не приходит снаружи — оно собирается из месяца, —
        поэтому подстановка в DDL безопасна: параметры в DDL PostgreSQL не
        принимает.
        """
        created = []
        async with self._errors():
            for month in months:
                name = partition_name(month)
                if await self._partition_exists(name):
                    continue
                start, end = partition_bounds(month)
                await self.session.execute(
                    text(
                        f'CREATE TABLE {SCHEMA}.{name} PARTITION OF {SCHEMA}.{TABLE} '
                        f"FOR VALUES FROM ('{start}') TO ('{end}')",
                    ),
                )
                created.append(name)
            await self.session.commit()
        return created

    async def _partition_exists(self, name: str) -> bool:
        found = await self.session.scalar(
            text('SELECT to_regclass(:qualified)').bindparams(qualified=f'{SCHEMA}.{name}'),
        )
        return found is not None


class PostgresSocialAccountRepository(PostgresRepository, SocialAccountRepository):
    async def get_user(self, provider: str, social_id: str) -> User | None:
        query = (
            select(UserRow)
            .join(SocialAccountRow, SocialAccountRow.user_id == UserRow.id)
            .where(SocialAccountRow.provider == provider, SocialAccountRow.social_id == social_id)
        )
        async with self._errors():
            row = await self.session.scalar(query)
        return User.model_validate(row) if row else None

    async def create_user(self, login: str, provider: str, profile: SocialProfile) -> User:
        async with self._errors():
            # Пароля нет: войти в такую учётную запись можно только соцсетью,
            # пока владелец сам не задаст пароль в личном кабинете.
            user_row = await self.session.scalar(
                insert(UserRow).values(login=login, password_hash=None).returning(UserRow),
            )
            await self.session.execute(
                insert(SocialAccountRow).values(**self._values(user_row.id, provider, profile)),
            )
            await self.session.commit()
        return User.model_validate(user_row)

    async def link(self, user_id: UUID, provider: str, profile: SocialProfile) -> SocialAccount:
        async with self._errors():
            row = await self.session.scalar(
                insert(SocialAccountRow).values(**self._values(user_id, provider, profile)).returning(SocialAccountRow),
            )
            await self.session.commit()
        return SocialAccount.model_validate(row)

    async def list_for_user(self, user_id: UUID) -> list[SocialAccount]:
        query = (
            select(SocialAccountRow)
            .where(SocialAccountRow.user_id == user_id)
            .order_by(SocialAccountRow.provider)
        )
        async with self._errors():
            rows = await self.session.scalars(query)
        return [SocialAccount.model_validate(row) for row in rows]

    async def unlink(self, user_id: UUID, provider: str) -> UnlinkResult:
        async with self._errors():
            result = await self._unlink_locked(user_id, provider)
            # Транзакция закрывается в любом случае: она держит блокировку
            # строки пользователя, даже если ничего не удалила.
            await self.session.commit()
        return result

    async def _unlink_locked(self, user_id: UUID, provider: str) -> UnlinkResult:
        """Считает оставшиеся способы войти и удаляет аккаунт, заблокировав строку пользователя.

        SELECT ... FOR UPDATE выстраивает одновременные открепления в очередь:
        второй запрос дождётся первого и увидит уже обновлённый список
        аккаунтов, а не тот, что был до него.
        """
        row = (
            await self.session.execute(
                select(UserRow.password_hash).where(UserRow.id == user_id).with_for_update(),
            )
        ).first()
        if row is None:
            return UnlinkResult.NOT_LINKED
        linked = set(
            await self.session.scalars(
                select(SocialAccountRow.provider).where(SocialAccountRow.user_id == user_id),
            ),
        )
        if provider not in linked:
            return UnlinkResult.NOT_LINKED
        if row.password_hash is None and linked == {provider}:
            return UnlinkResult.LAST_LOGIN_METHOD
        await self.session.execute(
            delete(SocialAccountRow).where(
                SocialAccountRow.user_id == user_id, SocialAccountRow.provider == provider,
            ),
        )
        return UnlinkResult.UNLINKED

    @staticmethod
    def _values(user_id: UUID, provider: str, profile: SocialProfile) -> dict[str, Any]:
        return {
            'user_id': user_id,
            'provider': provider,
            'social_id': profile.social_id,
            'display_name': profile.display_name,
            'email': profile.email,
        }
