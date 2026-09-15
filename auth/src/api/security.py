"""Кто делает запрос: пользователь из access-токена или аноним."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.dependencies import AccessServiceDep, AuthServiceDep
from models.role import MANAGE_ACCESS
from services.auth import Principal
from services.errors import NotAuthenticatedError

# auto_error=False: без токена FastAPI ответил бы своей ошибкой, а нужна
# единая для сервиса not_authenticated. Анонимные запросы тоже разрешены там,
# где токен необязателен (проверка прав).
bearer = HTTPBearer(
    auto_error=False,
    scheme_name='accessToken',
    description='access-токен из ответа на вход или обновление токенов',
)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


async def get_principal(credentials: Credentials, auth: AuthServiceDep) -> Principal:
    if credentials is None:
        raise NotAuthenticatedError
    return await auth.authenticate(credentials.credentials)


async def get_optional_principal(credentials: Credentials, auth: AuthServiceDep) -> Principal | None:
    """Пользователь из токена или None для анонима; недействительный токен — ошибка, а не аноним."""
    if credentials is None:
        return None
    return await auth.authenticate(credentials.credentials)


PrincipalDep = Annotated[Principal, Depends(get_principal)]
OptionalPrincipalDep = Annotated[Principal | None, Depends(get_optional_principal)]


async def require_manage_access(principal: PrincipalDep, access: AccessServiceDep) -> Principal:
    # Отобранное право управлять доступом должно перестать действовать сразу,
    # поэтому оно проверяется по базе, а не по кешу. Запросов на управление
    # ролями немного — лишний запрос к PostgreSQL здесь ничего не стоит.
    await access.require(principal, MANAGE_ACCESS, fresh=True)
    return principal


AdminDep = Annotated[Principal, Depends(require_manage_access)]
