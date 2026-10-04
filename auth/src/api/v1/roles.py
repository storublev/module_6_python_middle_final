from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter

from api.dependencies import RoleServiceDep
from api.errors import ADMIN_ERRORS, error_responses
from api.security import AdminDep
from api.v1.schemas import RoleCreateSchema, RoleSchema, RoleUpdateSchema
from models.role import Role
from services.errors import RoleNameTakenError, RoleNotFoundError

router = APIRouter()


@router.get(
    '',
    response_model=list[RoleSchema],
    summary='Список ролей',
    responses=error_responses(*ADMIN_ERRORS),
)
async def list_roles(_: AdminDep, roles: RoleServiceDep) -> list[Role]:
    return await roles.list_roles()


@router.post(
    '',
    status_code=HTTPStatus.CREATED,
    response_model=RoleSchema,
    summary='Создание роли',
    responses=error_responses(*ADMIN_ERRORS, RoleNameTakenError),
)
async def create_role(body: RoleCreateSchema, _: AdminDep, roles: RoleServiceDep) -> Role:
    return await roles.create_role(body.name, body.description, body.permissions)


@router.get(
    '/{role_id}',
    response_model=RoleSchema,
    summary='Роль',
    responses=error_responses(*ADMIN_ERRORS, RoleNotFoundError),
)
async def get_role(role_id: UUID, _: AdminDep, roles: RoleServiceDep) -> Role:
    return await roles.get_role(role_id)


@router.patch(
    '/{role_id}',
    response_model=RoleSchema,
    summary='Изменение роли',
    description='Меняет переданные поля. Новые права действуют сразу у всех пользователей с этой ролью.',
    responses=error_responses(*ADMIN_ERRORS, RoleNotFoundError, RoleNameTakenError),
)
async def update_role(role_id: UUID, body: RoleUpdateSchema, _: AdminDep, roles: RoleServiceDep) -> Role:
    return await roles.update_role(role_id, body.model_dump(exclude_unset=True))


@router.delete(
    '/{role_id}',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Удаление роли',
    description='Удаляет роль и отбирает её у всех пользователей.',
    responses=error_responses(*ADMIN_ERRORS, RoleNotFoundError),
)
async def delete_role(role_id: UUID, _: AdminDep, roles: RoleServiceDep) -> None:
    await roles.delete_role(role_id)
