from datetime import timedelta
from uuid import UUID

from models.role import UserAccess
from services.auth import Principal
from services.errors import PermissionDeniedError
from storage.base import AccessCache, UserRepository

# Анонимный пользователь не обладает никакими правами. Ему доступно всё, для
# чего право не требуется: ограничивать нужно только помеченные ресурсы.
ANONYMOUS = UserAccess()


class AccessService:
    """Проверка прав пользователя.

    Проверка нужна почти каждому запросу к кинотеатру, поэтому права
    пользователя читаются из PostgreSQL один раз и дальше берутся из кеша.
    Назначение и отзыв роли сбрасывают кеш пользователя, изменение и
    удаление роли — кеш всех пользователей, так что права действуют сразу.

    Сброс кеша — отдельный от изменения роли шаг: если Redis был недоступен,
    кеш сбросит фоновый повтор, а до того в кеше могут остаться прежние права.
    Для действий, где отзыв права должен действовать без такой задержки
    (управление доступом), проверка идёт по актуальным правам из PostgreSQL
    (fresh=True).
    """

    def __init__(self, users: UserRepository, cache: AccessCache, cache_ttl: timedelta):
        self.users = users
        self.cache = cache
        self.cache_ttl = cache_ttl

    async def get_access(self, principal: Principal | None, fresh: bool = False) -> UserAccess:
        if principal is None:
            return ANONYMOUS
        if fresh:
            return await self._read(principal.user_id)
        return await self._load(principal.user_id)

    async def check(self, principal: Principal | None, permission: str, fresh: bool = False) -> bool:
        """Есть ли у пользователя право; суперпользователю разрешено всё.

        fresh=True — по актуальным правам из базы, минуя кеш.
        """
        access = await self.get_access(principal, fresh=fresh)
        return access.allows(permission)

    async def require(self, principal: Principal | None, permission: str, fresh: bool = False) -> None:
        """Проверяет право и поднимает PermissionDeniedError, если его нет."""
        if not await self.check(principal, permission, fresh=fresh):
            raise PermissionDeniedError(f'Permission {permission} is required')

    async def _load(self, user_id: UUID) -> UserAccess:
        access, version = await self.cache.get(user_id)
        if access is not None:
            return access
        access = await self._read(user_id)
        await self.cache.set(user_id, access, version, ttl=self.cache_ttl)
        return access

    async def _read(self, user_id: UUID) -> UserAccess:
        # Пользователя могли удалить, пока его токен ещё действует: прав у него нет.
        return await self.users.get_access(user_id) or ANONYMOUS
