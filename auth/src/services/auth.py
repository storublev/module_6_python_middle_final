import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from models.session import Session
from models.user import User
from services.errors import InvalidCredentialsError, LoginTakenError, TokenRevokedError
from services.passwords import PasswordHasher
from services.throttling import Throttle
from services.tokens import TokenClaims, TokenPair, TokenService, TokenType
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


class SignupService:
    """Регистрация на сайте: создание учётной записи с ограничением частоты по IP.

    Консольная команда создаёт пользователей через RegistrationService
    напрямую: у неё нет ни клиента, ни лимитов.
    """

    def __init__(self, registration: RegistrationService, throttle: Throttle):
        self.registration = registration
        self.throttle = throttle

    async def signup(self, login: str, password: str, client: ClientInfo) -> User:
        """Создаёт пользователя.

        Raises:
            TooManyRequestsError: слишком много регистраций с адреса клиента.
            LoginTakenError: логин занят.
        """
        await self.throttle.signup_attempt(client.ip)
        return await self.registration.register(login, password)


class AuthService:
    """Вход, обновление токенов, проверка access-токена и выход."""

    def __init__(
        self,
        users: UserRepository,
        sessions: SessionStore,
        history: LoginHistoryRepository,
        tokens: TokenService,
        passwords: PasswordHasher,
        throttle: Throttle,
    ):
        self.users = users
        self.sessions = sessions
        self.history = history
        self.tokens = tokens
        self.passwords = passwords
        self.throttle = throttle

    async def login(self, login: str, password: str, client: ClientInfo) -> TokenPair:
        """Проверяет логин и пароль, открывает сессию и записывает вход в историю.

        Попытка засчитывается в лимиты до проверки пароля: сверх лимита хеш
        Argon2 не считается вовсе.

        Raises:
            TooManyRequestsError: слишком много попыток с адреса клиента или для логина.
            InvalidCredentialsError: нет такого логина или пароль неверный.
        """
        await self.throttle.login_attempt(login, client.ip)
        user = await self.users.get_by_login(login)
        if user is None or not user.has_password:
            # Пароля нет и у пользователя, заведённого соцсетью: сравниваем с
            # заглушкой, чтобы по времени ответа нельзя было отличить такой
            # аккаунт от несуществующего.
            await self.passwords.verify_dummy(password)
            raise InvalidCredentialsError
        # Сюда доходим, только если хеш есть: ветка выше отсеяла аккаунты
        # без пароля, но mypy этого через has_password не видит.
        assert user.password_hash is not None  # noqa: S101
        if not await self.passwords.verify(password, user.password_hash):
            raise InvalidCredentialsError
        await self.throttle.login_succeeded(login)
        return await self.open_session(user, client)

    async def open_session(self, user: User, client: ClientInfo) -> TokenPair:
        """Открывает сессию пользователю и записывает вход в историю.

        Отдельно от login, потому что входом по паролю способы войти не
        исчерпываются: вход через соцсеть подтверждает личность у поставщика,
        а дальше сессия открывается точно так же.
        """
        session_id = uuid4()
        tokens = self.tokens.issue(user.id, session_id)
        # Если пароль сменят, пока открывается сессия, она получит прежнюю
        # версию учётных данных и действовать не будет.
        session = Session(
            id=session_id,
            user_id=user.id,
            refresh_jti=tokens.refresh_jti,
            credentials_version=user.credentials_version,
        )
        await self.sessions.create(session, ttl=self.tokens.refresh_ttl)
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
            TokenRevokedError: сессия закрыта, открыта до смены пароля или токен уже использован.
        """
        claims = self.tokens.decode(refresh_token, TokenType.REFRESH)
        await self._check_session(claims)
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
            TokenRevokedError: пользователь вышел из этой сессии или сессия открыта до смены пароля.
        """
        claims = self.tokens.decode(access_token, TokenType.ACCESS)
        await self._check_session(claims)
        return Principal(user_id=claims.user_id, session_id=claims.session_id)

    async def ensure_session(self, principal: Principal) -> int:
        """Проверяет, что сессия жива и открыта с текущей версией учётных данных; возвращает версию.

        Версия хранится в PostgreSQL и меняется в одной транзакции с паролем.
        Поэтому смена пароля закрывает остальные сессии, даже если удалить
        их из Redis не удалось: такие сессии отвергаются здесь.

        Проверка нужна не только при аутентификации: операция, начатая с
        действующим токеном, может завершиться сильно позже — к этому времени
        вход, с которого её начали, мог уже не действовать.

        Raises:
            TokenRevokedError: сессия закрыта или открыта до смены пароля.
        """
        session = await self.sessions.get(principal.session_id)
        if session is None or session.user_id != principal.user_id:
            raise TokenRevokedError
        # None — пользователя удалили, пока его сессия ещё жила.
        version = await self.users.get_credentials_version(principal.user_id)
        if version != session.credentials_version:
            await self.sessions.delete(principal.user_id, principal.session_id)
            raise TokenRevokedError
        return version

    async def _check_session(self, claims: TokenClaims) -> None:
        """Сессия токена жива и открыта с текущей версией учётных данных пользователя."""
        await self.ensure_session(Principal(user_id=claims.user_id, session_id=claims.session_id))

    async def logout(self, principal: Principal) -> None:
        """Закрывает текущую сессию: её access- и refresh-токены перестают действовать."""
        await self.sessions.delete(principal.user_id, principal.session_id)

    async def logout_others(self, principal: Principal) -> int:
        """Закрывает все сессии пользователя, кроме текущей; возвращает, сколько закрыто."""
        return await self.sessions.delete_others(principal.user_id, keep_session_id=principal.session_id)
