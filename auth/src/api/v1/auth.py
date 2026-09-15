from http import HTTPStatus

from fastapi import APIRouter, Request

from api.dependencies import AuthServiceDep, SignupServiceDep
from api.errors import TOKEN_ERRORS, error_responses
from api.security import PrincipalDep
from api.v1.schemas import LoginSchema, RefreshSchema, SignupSchema, TokenPairSchema, UserSchema
from models.user import User, normalize_login
from services.auth import ClientInfo
from services.errors import (
    InvalidCredentialsError,
    LoginTakenError,
    TokenExpiredError,
    TokenInvalidError,
    TokenRevokedError,
    TooManyRequestsError,
)
from services.tokens import TokenPair

router = APIRouter()


def client_info(request: Request) -> ClientInfo:
    # Адрес клиента за nginx uvicorn берёт из X-Forwarded-For, который nginx
    # перезаписывает адресом клиента: подставить чужой IP через заголовок нельзя.
    return ClientInfo(
        user_agent=request.headers.get('user-agent'),
        ip=request.client.host if request.client else None,
    )


@router.post(
    '/signup',
    status_code=HTTPStatus.CREATED,
    response_model=UserSchema,
    summary='Регистрация',
    description='Создаёт пользователя. Логин приводится к нижнему регистру и должен быть свободен. '
                'Число регистраций с одного IP ограничено: сверх лимита — 429 с заголовком Retry-After.',
    responses=error_responses(LoginTakenError, TooManyRequestsError),
)
async def signup(body: SignupSchema, request: Request, signups: SignupServiceDep) -> User:
    return await signups.signup(body.login, body.password, client_info(request))


@router.post(
    '/login',
    response_model=TokenPairSchema,
    summary='Вход',
    description='Обменивает логин и пароль на пару токенов и записывает вход в историю. '
                'Каждый вход открывает отдельную сессию: так входят с разных устройств. '
                'Число попыток ограничено для IP и для логина (успешный вход обнуляет счётчик логина): '
                'сверх лимита — 429 с заголовком Retry-After, пароль при этом не проверяется.',
    responses=error_responses(InvalidCredentialsError, TooManyRequestsError),
)
async def login(body: LoginSchema, request: Request, auth: AuthServiceDep) -> TokenPair:
    return await auth.login(normalize_login(body.login), body.password, client_info(request))


@router.post(
    '/token/refresh',
    response_model=TokenPairSchema,
    summary='Обновление токенов',
    description='Обменивает refresh-токен на новую пару. refresh-токен одноразовый: если предъявить '
                'уже использованный, сессия закроется — его мог перехватить злоумышленник.',
    responses=error_responses(TokenExpiredError, TokenInvalidError, TokenRevokedError),
)
async def refresh(body: RefreshSchema, auth: AuthServiceDep) -> TokenPair:
    return await auth.refresh(body.refresh_token)


@router.post(
    '/logout',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Выход',
    description='Закрывает текущую сессию: её access- и refresh-токены перестают действовать.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def logout(principal: PrincipalDep, auth: AuthServiceDep) -> None:
    await auth.logout(principal)


@router.post(
    '/logout/others',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Выход из остальных сессий',
    description='Закрывает все сессии пользователя, кроме текущей: на других устройствах придётся войти заново.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def logout_others(principal: PrincipalDep, auth: AuthServiceDep) -> None:
    await auth.logout_others(principal)
