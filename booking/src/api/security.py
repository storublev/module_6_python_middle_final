"""Опознание зрителя по access-токену сервиса авторизации.

Токен проверяется на месте: подпись тем же секретом, которым выпущена, срок
действия и тип. На чтение этого достаточно, и в сервис авторизации чтение не
ходит — по той же причине, что и в остальных сервисах кинотеатра (ADR-4):
страницы каталога и кабинета не должны ждать Auth и падать вместе с ним.

**Запись проверяется строже** (`ActiveUser`): создать, изменить и отменить
показ, забронировать, изменить и отменить бронь, оценить. Подпись остаётся
верной до конца срока токена (до 15 минут) и после выхода или смены пароля, а
украденным токеном за эти минуты можно отменить чужой показ или чужую бронь.
Поэтому перед записью сессия сверяется с сервисом авторизации. Записей на
порядок меньше, чем чтений, и лишний запрос в Auth им по карману. Сервис
авторизации не ответил — запись отклоняется с 503: выпустить действие от
имени, которое нельзя подтвердить, хуже, чем попросить повторить позже.

Всё знание о формате токена собрано здесь. Когда подпись сменится на
асимметричную (RS256), менять придётся только этот модуль.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import Settings, settings
from services.errors import NotAuthenticatedError, TokenExpiredError, TokenInvalidError, TokenRevokedError
from storage.base import Sessions

# Те же обязательные поля, что выпускает сервис авторизации. Требовать их явно
# нужно, чтобы токен без `exp` не оказался вечным.
REQUIRED_CLAIMS = ['sub', 'sid', 'jti', 'type', 'iat', 'exp']
# Значение поля type в токене. Линтер принимает его за пароль из-за имени.
ACCESS_TOKEN_TYPE = 'access'  # noqa: S105
SCHEME = 'Bearer'


@dataclass(frozen=True)
class AuthenticatedUser:
    """Кто прислал запрос: владелец токена и его сессия входа."""

    user_id: UUID
    session_id: UUID


class TokenVerifier:
    """Проверка access-токена сервиса авторизации."""

    def __init__(self, secret_key: str, algorithm: str) -> None:
        self._secret_key = secret_key
        self._algorithm = algorithm

    def verify(self, authorization_header: str | None) -> AuthenticatedUser:
        """Достаёт зрителя из заголовка `Authorization: Bearer <token>`.

        Raises:
            NotAuthenticatedError: заголовка нет или он не того формата.
            TokenExpiredError: срок действия токена истёк.
            TokenInvalidError: подпись, формат или тип токена не подходят.
        """
        token = self._extract(authorization_header)
        try:
            # Алгоритм задан явно: токен с alg=none или подписанный чужим
            # алгоритмом не пройдёт.
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
                options={'require': REQUIRED_CLAIMS},
                leeway=0,
            )
        except jwt.ExpiredSignatureError as error:
            raise TokenExpiredError from error
        except jwt.InvalidTokenError as error:
            raise TokenInvalidError from error

        if payload.get('type') != ACCESS_TOKEN_TYPE:
            # Refresh-токеном бронировать нельзя: он живёт две недели, и его
            # утечка стоила бы дороже.
            raise TokenInvalidError('Access token is expected')
        try:
            return AuthenticatedUser(user_id=UUID(payload['sub']), session_id=UUID(payload['sid']))
        except (TypeError, ValueError) as error:
            raise TokenInvalidError('Access token claims are malformed') from error

    @staticmethod
    def _extract(header: str | None) -> str:
        if not header:
            raise NotAuthenticatedError
        scheme, _, token = header.partition(' ')
        if scheme.lower() != SCHEME.lower() or not token.strip():
            raise NotAuthenticatedError(f'Authorization header must be "{SCHEME} <token>"')
        return token.strip()


def get_verifier(config: Settings | None = None) -> TokenVerifier:
    """Собирает проверяющего токены: секрет общий с сервисом авторизации."""
    config = config or settings
    return TokenVerifier(config.jwt_secret_key.get_secret_value(), config.jwt_algorithm)


# Схема нужна не для проверки, а для документации: с ней в Swagger появляется
# кнопка Authorize, и защищённые эндпоинты можно попробовать прямо из браузера.
# `auto_error=False` — потому что отказы мы отдаём в общем формате сервиса
# (`{"code": ..., "detail": ...}`), а не в стандартном формате FastAPI.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name='Access-токен',
    description='Access-токен сервиса авторизации: `POST /auth/api/v1/login` вернёт его в `access_token`.',
)


def current_user(
    request: Request,
    _credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> AuthenticatedUser:
    """Зависимость для эндпоинтов, где токен обязателен.

    Заголовок читается из запроса напрямую, а не из `_credentials`: так
    сохраняются наши сообщения об ошибках для заголовка неверного формата.
    """
    verifier: TokenVerifier = request.app.state.verifier
    return verifier.verify(request.headers.get('Authorization'))


CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]


async def active_user(request: Request, user: CurrentUser) -> AuthenticatedUser:
    """Зависимость для записи: токен верен **и** его сессия не закрыта.

    Raises:
        TokenRevokedError: сессия закрыта выходом или сменой пароля.
        StorageUnavailableError: сервис авторизации не ответил (API отдаст 503).
    """
    sessions: Sessions = request.app.state.sessions
    # Заголовок уже проверен в current_user: он есть и он нужного формата.
    if not await sessions.is_active(request.headers['Authorization']):
        raise TokenRevokedError
    return user


ActiveUser = Annotated[AuthenticatedUser, Depends(active_user)]


def optional_authorization(request: Request) -> str | None:
    """Заголовок Authorization как есть — его сервис передаёт каталогу (подписочные фильмы)."""
    return request.headers.get('Authorization')
