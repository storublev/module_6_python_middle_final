"""Опознание пользователя по access-токену сервиса авторизации.

Токен проверяется на месте: подпись тем же секретом, которым выпущена, срок
действия и тип. Сетевого вызова в сервис авторизации нет — почему так, описано
в ADR-4: при пиковых 420 запросах в секунду он стал бы узким местом и точкой
отказа для сбора событий, а цена ошибки здесь невелика — события вышедшего
пользователя дойдут до аналитики ещё несколько минут, пока жив его токен.

Всё знание о формате токена собрано здесь. Когда подпись сменится на
асимметричную (RS256), менять придётся только этот модуль.
"""

from dataclasses import dataclass
from http import HTTPStatus
from uuid import UUID

import jwt

from api.errors import ApiError

# Те же обязательные поля, что выпускает сервис авторизации. Требовать их
# явно нужно, чтобы токен без `exp` не оказался вечным.
REQUIRED_CLAIMS = ['sub', 'sid', 'jti', 'type', 'iat', 'exp']
# Значение поля type в токене. Линтер принимает его за пароль из-за имени —
# отсюда noqa.
ACCESS_TOKEN_TYPE = 'access'  # noqa: S105
SCHEME = 'Bearer'


@dataclass(frozen=True)
class AuthenticatedUser:
    """Кто прислал события: владелец токена и его сессия входа."""

    user_id: UUID
    session_id: UUID


class TokenVerifier:
    """Проверка access-токена сервиса авторизации."""

    def __init__(self, secret_key: str, algorithm: str):
        self._secret_key = secret_key
        self._algorithm = algorithm

    def verify(self, authorization_header: str | None) -> AuthenticatedUser:
        """Достаёт пользователя из заголовка `Authorization: Bearer <token>`.

        Raises:
            ApiError: заголовка нет, он не того формата или токен не принят.
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
            raise ApiError(HTTPStatus.UNAUTHORIZED, 'token_expired', 'Access token has expired') from error
        except jwt.InvalidTokenError as error:
            raise ApiError(HTTPStatus.UNAUTHORIZED, 'token_invalid', 'Access token is invalid') from error

        if payload.get('type') != ACCESS_TOKEN_TYPE:
            # Refresh-токеном события слать нельзя: он живёт две недели, и его
            # утечка стоила бы дороже.
            raise ApiError(HTTPStatus.UNAUTHORIZED, 'token_invalid', 'Access token is expected')
        try:
            return AuthenticatedUser(user_id=UUID(payload['sub']), session_id=UUID(payload['sid']))
        except (TypeError, ValueError) as error:
            raise ApiError(HTTPStatus.UNAUTHORIZED, 'token_invalid', 'Access token claims are malformed') from error

    @staticmethod
    def _extract(header: str | None) -> str:
        if not header:
            raise ApiError(HTTPStatus.UNAUTHORIZED, 'not_authenticated', 'Authorization header is required')
        scheme, _, token = header.partition(' ')
        if scheme.lower() != SCHEME.lower() or not token.strip():
            raise ApiError(
                HTTPStatus.UNAUTHORIZED,
                'not_authenticated',
                f'Authorization header must be "{SCHEME} <token>"',
            )
        return token.strip()
