import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from models.session import Session
from models.user import User
from services.errors import InvalidCredentialsError, LoginTakenError, TokenRevokedError
from services.passwords import PasswordHasher
from services.tokens import TokenPair, TokenService, TokenType
from storage.base import AlreadyExistsError, LoginHistoryRepository, RotateResult, SessionStore, UserRepository

logger = logging.getLogger(__name__)

USER_AGENT_MAX_LENGTH = 512


@dataclass(frozen=True)
class Principal:
    """Аутентифицированный пользователь запроса и сессия, из которой он пришёл."""

    user_id: UUID
    session_id: UUID


@dataclass(frozen=True)
class ClientInfo:
    """С какого устройства выполняется вход: попадает в историю входов."""

    user_agent: str | None
    ip: str | None


class RegistrationService:
    """Создание учётных записей: регистрация на сайте и суперпользователь из консоли."""

    def __init__(self, users: UserRepository, passwords: PasswordHasher):
        self.users = users
        self.passwords = passwords

    async def register(self, login: str, password: str, is_superuser: bool = False) -> User:
        """Создаёт пользователя с хешем пароля.

        Raises:
            LoginTakenError: логин занят.
        """
        password_hash = await self.passwords.hash(password)
        try:
            return await self.users.create(login, password_hash, is_superuser=is_superuser)
        except AlreadyExistsError as exc:
            raise LoginTakenError from exc


class AuthService:
    """Вход, обновление токенов, проверка access-токена и выход."""

    def __init__(
        self,
        users: UserRepository,
        sessions: SessionStore,
        history: LoginHistoryRepository,
        tokens: TokenService,
        passwords: PasswordHasher,
    ):
        self.users = users
        self.sessions = sessions
        self.history = history
        self.tokens = tokens
        self.passwords = passwords

    async def login(self, login: str, password: str, client: ClientInfo) -> TokenPair:
        """Проверяет логин и пароль, открывает сессию и записывает вход в историю.

        Raises:
            InvalidCredentialsError: нет такого логина или пароль неверный.
        """
        user = await self.users.get_by_login(login)
        if user is None:
            await self.passwords.verify_dummy(password)
            raise InvalidCredentialsError
        if not await self.passwords.verify(password, user.password_hash):
            raise InvalidCredentialsError

        session_id = uuid4()
        tokens = self.tokens.issue(user.id, session_id)
        await self.sessions.create(
            Session(id=session_id, user_id=user.id, refresh_jti=tokens.refresh_jti),
            ttl=self.tokens.refresh_ttl,
        )
        user_agent = client.user_agent[:USER_AGENT_MAX_LENGTH] if client.user_agent else None
        await self.history.add(user.id, user_agent=user_agent, ip=client.ip)
        return tokens

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Обменивает refresh-токен на новую пару; старый refresh-токен больше не действует.

        Повторное предъявление уже использованного refresh-токена значит, что
        его мог перехватить злоумышленник: сессия закрывается целиком, и
        войти заново придётся и владельцу, и тому, кто завладел токеном.

        Raises:
            TokenExpiredError, TokenInvalidError: токен истёк или недействителен.
            TokenRevokedError: сессия закрыта или токен уже использован.
        """
        claims = self.tokens.decode(refresh_token, TokenType.REFRESH)
        tokens = self.tokens.issue(claims.user_id, claims.session_id)
        result = await self.sessions.rotate(
            claims.user_id, claims.session_id, claims.jti, tokens.refresh_jti, ttl=self.tokens.refresh_ttl,
        )
        if result is RotateResult.MISSING:
            raise TokenRevokedError
        if result is RotateResult.REUSED:
            logger.warning('Повторно предъявлен refresh-токен сессии %s, сессия закрыта', claims.session_id)
            await self.sessions.delete(claims.user_id, claims.session_id)
            raise TokenRevokedError('Refresh token has already been used, the session has been terminated')
        return tokens

    async def authenticate(self, access_token: str) -> Principal:
        """Проверяет access-токен: подпись, срок, тип и то, что его сессия не закрыта.

        Raises:
            TokenExpiredError, TokenInvalidError: токен истёк или недействителен.
            TokenRevokedError: пользователь вышел из этой сессии.
        """
        claims = self.tokens.decode(access_token, TokenType.ACCESS)
        if not await self.sessions.exists(claims.session_id):
            raise TokenRevokedError
        return Principal(user_id=claims.user_id, session_id=claims.session_id)

    async def logout(self, principal: Principal) -> None:
        """Закрывает текущую сессию: её access- и refresh-токены перестают действовать."""
        await self.sessions.delete(principal.user_id, principal.session_id)

    async def logout_others(self, principal: Principal) -> int:
        """Закрывает все сессии пользователя, кроме текущей; возвращает, сколько закрыто."""
        return await self.sessions.delete_others(principal.user_id, keep_session_id=principal.session_id)
