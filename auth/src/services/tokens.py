"""Выпуск и проверка JWT.

Оба токена — JWT одного формата, различаются типом (`type`) и временем жизни:

* `sub` — id пользователя, `sid` — id сессии (одного входа на одном
  устройстве), `jti` — уникальный id токена, `iat` и `exp` — когда выпущен и
  до какого момента действует;
* access-токен короткоживущий и нигде не хранится;
* refresh-токен одноразовый: действует только тот, чей jti записан в сессии.

JWT не шифруется, а подписывается: payload может прочитать кто угодно,
поэтому в нём нет ничего секретного.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

import jwt

from services.errors import TokenExpiredError, TokenInvalidError

REQUIRED_CLAIMS = ['sub', 'sid', 'jti', 'type', 'iat', 'exp']


class TokenType(StrEnum):
    ACCESS = 'access'
    REFRESH = 'refresh'


@dataclass(frozen=True)
class TokenClaims:
    user_id: UUID
    session_id: UUID
    jti: str
    type: TokenType


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    refresh_jti: str
    # Через сколько секунд истечёт access-токен: клиенту удобно обновить пару заранее.
    expires_in: int


class TokenService:
    def __init__(
        self,
        secret_key: str,
        algorithm: str,
        access_ttl: timedelta,
        refresh_ttl: timedelta,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        self.clock = clock

    def issue(self, user_id: UUID, session_id: UUID) -> TokenPair:
        """Выпускает пару токенов для сессии."""
        now = self.clock()
        refresh_jti = uuid4().hex
        return TokenPair(
            access_token=self._encode(user_id, session_id, uuid4().hex, TokenType.ACCESS, now, self.access_ttl),
            refresh_token=self._encode(user_id, session_id, refresh_jti, TokenType.REFRESH, now, self.refresh_ttl),
            refresh_jti=refresh_jti,
            expires_in=int(self.access_ttl.total_seconds()),
        )

    def decode(self, token: str, expected_type: TokenType) -> TokenClaims:
        """Проверяет подпись, срок действия и тип токена.

        Сначала проверяется подпись, потом срок: поддельный токен с истёкшим
        сроком получит token_invalid, а не token_expired.

        Raises:
            TokenExpiredError: подпись верна, но срок действия истёк.
            TokenInvalidError: токен повреждён, подделан или другого типа.
        """
        try:
            # Алгоритм задан явно: токен с alg=none или чужим алгоритмом не пройдёт.
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={'require': REQUIRED_CLAIMS},
                leeway=0,
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError(f'{expected_type.capitalize()} token has expired') from exc
        except jwt.InvalidSignatureError as exc:
            raise TokenInvalidError('Token signature is invalid') from exc
        except jwt.InvalidTokenError as exc:
            raise TokenInvalidError(f'Token is malformed: {exc}') from exc

        if payload['type'] != expected_type:
            raise TokenInvalidError(f'{expected_type.capitalize()} token is expected')
        try:
            return TokenClaims(
                user_id=UUID(payload['sub']),
                session_id=UUID(payload['sid']),
                jti=str(payload['jti']),
                type=TokenType(payload['type']),
            )
        except (TypeError, ValueError) as exc:
            raise TokenInvalidError('Token claims are malformed') from exc

    def _encode(
        self, user_id: UUID, session_id: UUID, jti: str, token_type: TokenType, now: datetime, ttl: timedelta,
    ) -> str:
        payload = {
            'sub': str(user_id),
            'sid': str(session_id),
            'jti': jti,
            'type': token_type.value,
            'iat': now,
            'exp': now + ttl,
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
