from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

ROLE_NAME_PATTERN = r'^[a-z][a-z0-9_-]*$'
ROLE_NAME_MAX_LENGTH = 64
# Право — действие над ресурсом через точку: films.subscription, access.manage.
PERMISSION_PATTERN = r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'
PERMISSION_MAX_LENGTH = 128

RoleName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=ROLE_NAME_MAX_LENGTH, pattern=ROLE_NAME_PATTERN),
]
Permission = Annotated[str, StringConstraints(max_length=PERMISSION_MAX_LENGTH, pattern=PERMISSION_PATTERN)]

# Права, о которых знает сам сервис авторизации.
MANAGE_ACCESS = 'access.manage'
# Право смотреть фильмы по подписке: ETL помечает их access_level=subscription,
# сервис контента проверяет это право перед выдачей такого фильма.
FILMS_SUBSCRIPTION = 'films.subscription'
# Право входить в админку каталога: её бэкенд аутентификации проверяет его
# после того, как сервис принял логин и пароль сотрудника.
ADMIN_ACCESS = 'admin.access'


class Role(BaseModel):
    """Роль — именованный набор прав, который выдаётся пользователям."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    name: str
    description: str | None
    permissions: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


class UserAccess(BaseModel):
    """Всё, что нужно для проверки прав пользователя: признак суперпользователя, роли и их права."""

    model_config = ConfigDict(frozen=True)

    is_superuser: bool = False
    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()

    def allows(self, permission: str) -> bool:
        """Суперпользователю разрешено всё, остальным — права их ролей."""
        return self.is_superuser or permission in self.permissions
