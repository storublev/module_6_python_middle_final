from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.dependencies import ProfileServiceDep
from api.errors import TOKEN_ERRORS, error_responses
from api.security import PrincipalDep
from api.v1.schemas import ChangeLoginSchema, ChangePasswordSchema, LoginRecordSchema, ProfileSchema, UserSchema
from models.user import LoginRecord, User
from services.errors import LoginTakenError, WrongPasswordError
from services.profile import Pagination

router = APIRouter()

MAX_PAGE_SIZE = 100


def get_pagination(
    page_number: Annotated[int, Query(ge=1, description='Номер страницы, начиная с 1')] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description='Записей на странице')] = 50,
) -> Pagination:
    return Pagination(page_number=page_number, page_size=page_size)


@router.get(
    '/me',
    response_model=ProfileSchema,
    summary='Данные пользователя',
    responses=error_responses(*TOKEN_ERRORS),
)
async def me(principal: PrincipalDep, profiles: ProfileServiceDep) -> ProfileSchema:
    profile = await profiles.get_profile(principal)
    return ProfileSchema.model_validate({**profile.user.model_dump(), 'roles': profile.roles})


@router.patch(
    '/me/login',
    response_model=UserSchema,
    summary='Смена логина',
    description='Меняет логин. Смену подтверждает текущий пароль.',
    responses=error_responses(*TOKEN_ERRORS, WrongPasswordError, LoginTakenError),
)
async def change_login(body: ChangeLoginSchema, principal: PrincipalDep, profiles: ProfileServiceDep) -> User:
    return await profiles.change_login(principal, body.new_login, body.password)


@router.put(
    '/me/password',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Смена пароля',
    description='Меняет пароль, подтверждённый текущим, и закрывает все сессии, кроме текущей: они перестают '
                'действовать сразу. Если в этот момент недоступно хранилище сессий, пароль всё равно сменится, '
                'а войти заново придётся и на текущем устройстве.',
    responses=error_responses(*TOKEN_ERRORS, WrongPasswordError),
)
async def change_password(body: ChangePasswordSchema, principal: PrincipalDep, profiles: ProfileServiceDep) -> None:
    await profiles.change_password(
        principal, body.password, body.new_password,
    )


@router.get(
    '/me/login-history',
    response_model=list[LoginRecordSchema],
    summary='История входов',
    description='Входы в аккаунт от новых к старым, постранично.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def login_history(
    principal: PrincipalDep,
    profiles: ProfileServiceDep,
    pagination: Annotated[Pagination, Depends(get_pagination)],
) -> list[LoginRecord]:
    return await profiles.login_history(principal, pagination)
