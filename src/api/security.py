"""Токен пользователя из заголовка Authorization.

Токен необязателен: каталог открыт анонимным пользователям, им доступны
публичные фильмы. Поэтому `auto_error=False` — без заголовка FastAPI не
отвечает ошибкой, а отдаёт None, и запрос идёт дальше как анонимный.

Сам токен здесь не разбирается: что он значит, знает сервис авторизации
(см. services/access.py). Класс нужен, чтобы Swagger показал кнопку ввода
токена и пометил эндпоинты, где он что-то меняет.
"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(
    auto_error=False,
    scheme_name='Токен сервиса авторизации',
    description='access-токен из POST /auth/api/v1/login. Без него доступны только публичные фильмы.',
)


def get_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str | None:
    return credentials.credentials if credentials else None


TokenDep = Annotated[str | None, Depends(get_token)]
