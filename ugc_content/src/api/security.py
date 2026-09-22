"""Опознание зрителя по access-токену сервиса авторизации.

Токен проверяется на месте: подпись тем же секретом, которым выпущена, срок
действия и тип. Сетевого вызова в сервис авторизации нет — по той же причине,
что и в сервисе сбора событий (ADR-4): на каждое открытие карточки фильма
ходить в Auth значило бы сделать его узким местом и точкой отказа, а цена
ошибки невелика — вышедший зритель сможет ставить лайки ещё несколько минут,
пока жив его токен.

Всё знание о формате токена собрано здесь. Когда подпись сменится на
асимметричную (RS256), менять придётся только этот модуль.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request

from core.config import Settings, settings
from services.errors import NotAuthenticatedError, TokenExpiredError, TokenInvalidError

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
            # Refresh-токеном ставить лайки нельзя: он живёт две недели, и его
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


def current_user(request: Request) -> AuthenticatedUser:
    """Зависимость для эндпоинтов, где токен обязателен."""
    verifier: TokenVerifier = request.app.state.verifier
    return verifier.verify(request.headers.get('Authorization'))


CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
