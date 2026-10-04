from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from api.dependencies import DirectoryServiceDep, ProfileServiceDep, SocialAuthServiceDep
from api.errors import TOKEN_ERRORS, error_responses
from api.security import PrincipalDep
from api.v1.auth import client_info
from api.v1.schemas import (
    ChangeLoginSchema,
    ChangePasswordSchema,
    LoginRecordSchema,
    ProfileSchema,
    ProfileUpdateSchema,
    SocialAccountSchema,
    UserSchema,
)
from models.social import SocialAccount
from models.user import LoginRecord, ProfileUpdate, User
from services.errors import (
    LastLoginMethodError,
    LoginTakenError,
    PasswordAlreadySetError,
    SocialAccountNotLinkedError,
    TooManyRequestsError,
    UnknownTimezoneError,
    WrongPasswordError,
)
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
    '/me/profile',
    response_model=ProfileSchema,
    summary='Контакты и имя',
    description='Меняет почту, имя, фамилию и часовой пояс — то, из чего сервис уведомлений собирает письмо. '
                'Присылаются только изменяемые поля; пустая строка очищает поле. '
                'Часовой пояс задаётся именем из базы IANA (`Europe/Moscow`): по нему решается, '
                'в какое время суток писать, поэтому неизвестное имя отклоняется сразу.',
    responses=error_responses(*TOKEN_ERRORS, UnknownTimezoneError),
)
async def change_profile(
    body: ProfileUpdateSchema, principal: PrincipalDep, directory: DirectoryServiceDep, profiles: ProfileServiceDep,
) -> ProfileSchema:
    # Пустая строка — это «очистить поле», поэтому она превращается в None уже
    # здесь: в ProfileUpdate None означает «не трогать», и различать их должен
    # слой API, а не бизнес-логика.
    changes = ProfileUpdate(**{key: (value or None) for key, value in body.model_dump(exclude_unset=True).items()})
    await directory.update_profile(principal.user_id, changes)
    profile = await profiles.get_profile(principal)
    return ProfileSchema.model_validate({**profile.user.model_dump(), 'roles': profile.roles})


@router.patch(
    '/me/login',
    response_model=UserSchema,
    summary='Смена логина',
    description='Меняет логин. Смену подтверждает текущий пароль. Число проверок пароля ограничено '
                'для учётной записи и для IP: сверх лимита — 429 с заголовком Retry-After, '
                'и пароль при этом не проверяется.',
    responses=error_responses(*TOKEN_ERRORS, WrongPasswordError, LoginTakenError, TooManyRequestsError),
)
async def change_login(
    body: ChangeLoginSchema, request: Request, principal: PrincipalDep, profiles: ProfileServiceDep,
) -> User:
    return await profiles.change_login(principal, body.new_login, body.password, client_info(request))


@router.put(
    '/me/password',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Смена пароля',
    description='Меняет пароль, подтверждённый текущим, и закрывает все сессии, кроме текущей: они перестают '
                'действовать сразу. Если в этот момент недоступно хранилище сессий, пароль всё равно сменится, '
                'а войти заново придётся и на текущем устройстве. Число проверок текущего пароля ограничено '
                'для учётной записи и для IP: сверх лимита — 429 с заголовком Retry-After.',
    responses=error_responses(
        *TOKEN_ERRORS, WrongPasswordError, PasswordAlreadySetError, TooManyRequestsError,
    ),
)
async def change_password(
    body: ChangePasswordSchema, request: Request, principal: PrincipalDep, profiles: ProfileServiceDep,
) -> None:
    await profiles.change_password(
        principal, body.password, body.new_password, client_info(request),
    )


@router.get(
    '/me/social-accounts',
    response_model=list[SocialAccountSchema],
    summary='Связанные аккаунты соцсетей',
    description='Аккаунты соцсетей, через которые можно войти в эту учётную запись.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def social_accounts(principal: PrincipalDep, social: SocialAuthServiceDep) -> list[SocialAccount]:
    return await social.list_accounts(principal.user_id)


@router.delete(
    '/me/social-accounts/{provider}',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Открепление аккаунта соцсети',
    description='Отвязывает аккаунт: войти через эту соцсеть больше нельзя. Последний способ войти '
                'открепить нельзя — сначала задайте пароль или привяжите другую соцсеть, иначе '
                'доступ к учётной записи будет потерян.',
    responses=error_responses(*TOKEN_ERRORS, SocialAccountNotLinkedError, LastLoginMethodError),
)
async def unlink_social_account(provider: str, principal: PrincipalDep, social: SocialAuthServiceDep) -> None:
    if not await social.unlink(principal.user_id, provider):
        raise SocialAccountNotLinkedError


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
