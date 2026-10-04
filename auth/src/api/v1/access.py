from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from api.dependencies import AccessServiceDep, RoleServiceDep
from api.errors import ADMIN_ERRORS, error_responses
from api.security import AdminDep, OptionalPrincipalDep
from api.v1.schemas import AccessCheckSchema, RoleSchema
from models.role import PERMISSION_MAX_LENGTH, PERMISSION_PATTERN, Role
from services.errors import (
    RoleNotAssignedError,
    RoleNotFoundError,
    TokenExpiredError,
    TokenInvalidError,
    TokenRevokedError,
    UserNotFoundError,
)

router = APIRouter()


@router.get(
    '/access/check',
    response_model=AccessCheckSchema,
    summary='Проверка права',
    description='Есть ли у пользователя из access-токена право. Токен необязателен: без него проверяется '
                'анонимный пользователь, у которого прав нет — ему доступно лишь то, что правом не ограничено. '
                'Суперпользователю разрешено всё. Права берутся из кеша, изменения ролей сбрасывают его сразу. '
                'Если в момент изменения хранилище кеша было недоступно, кеш сбросит фоновый повтор через '
                'несколько секунд; для действий, где отзыв права должен действовать без задержки, передайте '
                'fresh=true — права будут прочитаны из базы.',
    responses=error_responses(TokenExpiredError, TokenInvalidError, TokenRevokedError),
)
async def check_access(
    principal: OptionalPrincipalDep,
    access: AccessServiceDep,
    permission: Annotated[
        str,
        Query(max_length=PERMISSION_MAX_LENGTH, pattern=PERMISSION_PATTERN,
              description='Право', examples=['films.subscription']),
    ],
    fresh: Annotated[bool, Query(description='Проверить по актуальным правам из базы, минуя кеш')] = False,
) -> AccessCheckSchema:
    return AccessCheckSchema(
        user_id=principal.user_id if principal else None,
        permission=permission,
        allowed=await access.check(principal, permission, fresh=fresh),
    )


@router.get(
    '/users/{user_id}/roles',
    response_model=list[RoleSchema],
    summary='Роли пользователя',
    responses=error_responses(*ADMIN_ERRORS, UserNotFoundError),
)
async def user_roles(user_id: UUID, _: AdminDep, roles: RoleServiceDep) -> list[Role]:
    return await roles.user_roles(user_id)


@router.put(
    '/users/{user_id}/roles/{role_id}',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Назначение роли',
    description='Назначает роль пользователю. Повторное назначение ничего не меняет. Права действуют сразу.',
    responses=error_responses(*ADMIN_ERRORS, UserNotFoundError, RoleNotFoundError),
)
async def assign_role(user_id: UUID, role_id: UUID, _: AdminDep, roles: RoleServiceDep) -> None:
    await roles.assign(user_id, role_id)


@router.delete(
    '/users/{user_id}/roles/{role_id}',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Отзыв роли',
    responses=error_responses(*ADMIN_ERRORS, UserNotFoundError, RoleNotFoundError, RoleNotAssignedError),
)
async def revoke_role(user_id: UUID, role_id: UUID, _: AdminDep, roles: RoleServiceDep) -> None:
    await roles.revoke(user_id, role_id)
