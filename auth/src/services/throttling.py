"""Ограничение частоты входа и регистрации.

Вход, регистрация и подтверждение пароля в личном кабинете считают хеш
Argon2 — это десятки миллисекунд процессора и десятки мегабайт памяти на
запрос. Без ограничений через них можно перебирать пароли и нагружать сервис,
в том числе входом с несуществующим логином: для него пароль тоже сверяется с
хешем-заглушкой. Поэтому попытка засчитывается до проверки пароля, и сверх
лимита Argon2 не выполняется.

* Вход ограничен по IP (перебор с одного адреса по разным логинам) и по
  логину (перебор пароля одного аккаунта с разных адресов). Успешный вход
  обнуляет счётчик логина: у владельца аккаунта копятся только неудачные
  попытки, а злоумышленник, не зная пароля, обнулить его не может.
* Регистрация ограничена по IP отдельным, более строгим лимитом: успешных
  регистраций с одного адреса много не бывает.
* Смена логина и пароля подтверждается текущим паролем, поэтому ограничена
  так же: иначе завладевший токеном подбирал бы пароль через личный кабинет,
  минуя лимиты входа. Счёт идёт по учётной записи и по IP.

Счётчики общие для всех процессов и реплик сервиса — они в Redis.
"""

import math
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from services.errors import TooManyRequestsError
from storage.base import RateLimit, RateLimiter

# Адрес клиента неизвестен только при необычном запуске (не через uvicorn):
# такие запросы делят один общий лимит, а не обходят его.
UNKNOWN_IP = 'unknown'


@dataclass(frozen=True)
class Limit:
    """Не больше attempts попыток за скользящее окно period."""

    attempts: int
    period: timedelta


@dataclass(frozen=True)
class ThrottlingPolicy:
    login_per_ip: Limit
    login_per_account: Limit
    signup_per_ip: Limit
    password_check_per_ip: Limit
    password_check_per_account: Limit


class Throttle:
    def __init__(self, limiter: RateLimiter, policy: ThrottlingPolicy):
        self.limiter = limiter
        self.policy = policy

    async def login_attempt(self, login: str, ip: str | None) -> None:
        """Засчитывает попытку входа.

        Raises:
            TooManyRequestsError: исчерпан лимит адреса или логина.
        """
        await self._acquire(
            _limit(f'login:ip:{ip or UNKNOWN_IP}', self.policy.login_per_ip),
            _limit(f'login:account:{login}', self.policy.login_per_account),
        )

    async def login_succeeded(self, login: str) -> None:
        """Обнуляет счётчик неудачных попыток логина после успешного входа."""
        await self.limiter.reset(f'login:account:{login}')

    async def signup_attempt(self, ip: str | None) -> None:
        """Засчитывает попытку регистрации.

        Raises:
            TooManyRequestsError: исчерпан лимит адреса.
        """
        await self._acquire(_limit(f'signup:ip:{ip or UNKNOWN_IP}', self.policy.signup_per_ip))

    async def password_check_attempt(self, user_id: UUID, ip: str | None) -> None:
        """Засчитывает проверку пароля в личном кабинете.

        Считается по пользователю, а не по логину: в кабинете логин не
        предъявляют, а сам он в это время может меняться.

        Raises:
            TooManyRequestsError: исчерпан лимит адреса или учётной записи.
        """
        await self._acquire(
            _limit(f'password:ip:{ip or UNKNOWN_IP}', self.policy.password_check_per_ip),
            _limit(f'password:user:{user_id}', self.policy.password_check_per_account),
        )

    async def password_check_succeeded(self, user_id: UUID) -> None:
        """Обнуляет счётчик неверных паролей после верного: у владельца копятся только промахи."""
        await self.limiter.reset(f'password:user:{user_id}')

    async def _acquire(self, *limits: RateLimit) -> None:
        wait = await self.limiter.acquire(limits)
        if wait is not None:
            raise TooManyRequestsError(retry_after=max(1, math.ceil(wait.total_seconds())))


def _limit(key: str, limit: Limit) -> RateLimit:
    return RateLimit(key=key, limit=limit.attempts, period=limit.period)
