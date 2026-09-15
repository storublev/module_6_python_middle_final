from typing import Any
from uuid import UUID

from models.role import Role
from services.errors import RoleNameTakenError, RoleNotAssignedError, RoleNotFoundError, UserNotFoundError
from storage.base import AccessCache, AlreadyExistsError, RoleRepository, UserRepository


class RoleService:
    """Управление ролями и их назначением пользователям.

    Любое изменение, влияющее на права, сбрасывает кеш прав: иначе проверка
    доступа до истечения кеша отвечала бы по-старому.
    """

    def __init__(self, roles: RoleRepository, users: UserRepository, cache: AccessCache):
        self.roles = roles
        self.users = users
        self.cache = cache

    async def list_roles(self) -> list[Role]:
        return await self.roles.get_all()

    async def get_role(self, role_id: UUID) -> Role:
        role = await self.roles.get(role_id)
        if role is None:
            raise RoleNotFoundError
        return role

    async def create_role(self, name: str, description: str | None, permissions: list[str]) -> Role:
        try:
            return await self.roles.create(name, description, _unique(permissions))
        except AlreadyExistsError as exc:
            raise RoleNameTakenError from exc

    async def update_role(self, role_id: UUID, changes: dict[str, Any]) -> Role:
        """Меняет переданные поля роли.

        Raises:
            RoleNotFoundError: роли нет.
            RoleNameTakenError: имя занято другой ролью.
        """
        if 'permissions' in changes:
            changes = {**changes, 'permissions': _unique(changes['permissions'])}
        try:
            role = await self.roles.update(role_id, changes)
        except AlreadyExistsError as exc:
            raise RoleNameTakenError from exc
        if role is None:
            raise RoleNotFoundError
        if changes:
            await self.cache.invalidate_all()
        return role

    async def delete_role(self, role_id: UUID) -> None:
        if not await self.roles.delete(role_id):
            raise RoleNotFoundError
        await self.cache.invalidate_all()

    async def user_roles(self, user_id: UUID) -> list[Role]:
        await self._ensure_user(user_id)
        return await self.roles.list_for_user(user_id)

    async def assign(self, user_id: UUID, role_id: UUID) -> None:
        """Назначает роль; повторное назначение ничего не меняет.

        Raises:
            UserNotFoundError, RoleNotFoundError: нет пользователя или роли.
        """
        await self._ensure_user(user_id)
        await self.get_role(role_id)
        await self.roles.assign(user_id, role_id)
        await self.cache.invalidate_user(user_id)

    async def revoke(self, user_id: UUID, role_id: UUID) -> None:
        """Отбирает роль.

        Raises:
            UserNotFoundError, RoleNotFoundError: нет пользователя или роли.
            RoleNotAssignedError: роль не была назначена.
        """
        await self._ensure_user(user_id)
        await self.get_role(role_id)
        if not await self.roles.revoke(user_id, role_id):
            raise RoleNotAssignedError
        await self.cache.invalidate_user(user_id)

    async def _ensure_user(self, user_id: UUID) -> None:
        if await self.users.get(user_id) is None:
            raise UserNotFoundError


def _unique(permissions: list[str]) -> list[str]:
    """Права без повторов, в порядке добавления."""
    return list(dict.fromkeys(permissions))
