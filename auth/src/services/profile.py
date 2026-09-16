import logging
from dataclasses import dataclass

from models.role import Role
from models.user import LoginRecord, User
from services.auth import ClientInfo, Principal
from services.errors import (
    LoginTakenError,
    PasswordAlreadySetError,
    TokenRevokedError,
    WrongPasswordError,
)
from services.passwords import PasswordHasher
from services.throttling import Throttle
from storage.base import (
    AlreadyExistsError,
    LoginHistoryRepository,
    RoleRepository,
    SessionStore,
    StorageUnavailableError,
    UserRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pagination:
    page_number: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page_number - 1) * self.page_size


@dataclass(frozen=True)
class Profile:
    user: User
    roles: list[Role]


class ProfileService:
    """Личный кабинет: данные пользователя, смена логина и пароля, история входов."""

    def __init__(
        self,
        users: UserRepository,
        roles: RoleRepository,
        history: LoginHistoryRepository,
        sessions: SessionStore,
        passwords: PasswordHasher,
        throttle: Throttle,
    ):
        self.users = users
        self.roles = roles
        self.history = history
        self.sessions = sessions
        self.passwords = passwords
        self.throttle = throttle

    async def get_profile(self, principal: Principal) -> Profile:
        user = await self._get_user(principal)
        return Profile(user=user, roles=await self.roles.list_for_user(user.id))

    async def change_login(self, principal: Principal, new_login: str, password: str, client: ClientInfo) -> User:
        """Меняет логин. Смену подтверждает текущий пароль: одного украденного токена мало.

        Raises:
            TooManyRequestsError: слишком много проверок пароля для учётной записи или адреса.
            WrongPasswordError: текущий пароль неверный.
            LoginTakenError: логин занят другим пользователем.
        """
        user = await self._get_user(principal)
        await self._check_password(user, password, client)
        if new_login == user.login:
            return user
        try:
            return await self.users.update_login(user.id, new_login)
        except AlreadyExistsError as exc:
            raise LoginTakenError from exc

    async def change_password(
        self, principal: Principal, password: str | None, new_password: str, client: ClientInfo,
    ) -> None:
        """Меняет пароль и закрывает остальные сессии: если пароль узнал кто-то ещё, он потеряет доступ.

        Остальные сессии перестают действовать в момент смены пароля: вместе с
        ним в той же транзакции растёт версия учётных данных, а сессии с
        прежней версией не проходят проверку. Дальше в Redis текущая сессия
        переводится на новую версию, а остальные удаляются. Если Redis в этот
        момент недоступен, безопасность не страдает — чужие сессии всё равно не
        действуют, а войти заново (уже с новым паролем) придётся и на текущем
        устройстве. Поэтому такой сбой не делает смену пароля неудачной.

        У пользователя, заведённого входом через соцсеть, пароля нет и
        подтверждать смену нечем: он задаёт первый пароль, не передавая
        текущий. Личность в этом случае подтверждает сама сессия.

        Raises:
            TooManyRequestsError: слишком много проверок пароля для учётной записи или адреса.
            WrongPasswordError: текущий пароль неверный.
            PasswordAlreadySetError: пароль уже есть, но текущий не передан.
        """
        user = await self._authorize_password_change(principal, password, client)
        version = await self.users.update_password(user.id, await self.passwords.hash(new_password))
        try:
            await self.sessions.set_credentials_version(principal.session_id, version)
            await self.sessions.delete_others(user.id, keep_session_id=principal.session_id)
        except StorageUnavailableError as exc:
            logger.warning('Пароль пользователя %s сменён, но сессии в Redis не обновлены: %s', user.id, exc)

    async def login_history(self, principal: Principal, pagination: Pagination) -> list[LoginRecord]:
        return await self.history.get_page(principal.user_id, offset=pagination.offset, limit=pagination.page_size)

    async def _get_user(self, principal: Principal) -> User:
        user = await self.users.get(principal.user_id)
        if user is None:
            # Сессия пережила учётную запись — продолжать её нельзя.
            raise TokenRevokedError('User no longer exists')
        return user

    async def _check_password(self, user: User, password: str, client: ClientInfo) -> None:
        """Сверяет текущий пароль, засчитывая попытку до самой проверки.

        Иначе завладевший токеном перебирал бы пароль здесь: у личного
        кабинета свои эндпоинты, и лимиты входа их не прикрывают. Попытка
        засчитывается первой, поэтому сверх лимита Argon2 не считается вовсе.

        Raises:
            TooManyRequestsError: исчерпан лимит проверок.
            WrongPasswordError: пароль неверный или его вовсе нет.
        """
        await self.throttle.password_check_attempt(user.id, client.ip)
        # Пароля нет — подтверждать им нечего, и проверка не пройдена.
        if not user.has_password or not await self.passwords.verify(password, user.password_hash):
            raise WrongPasswordError
        await self.throttle.password_check_succeeded(user.id)

    async def _authorize_password_change(
        self, principal: Principal, password: str | None, client: ClientInfo,
    ) -> User:
        user = await self._get_user(principal)
        if not user.has_password:
            # Первый пароль пользователя из соцсети: подтверждать нечем.
            return user
        if password is None:
            raise PasswordAlreadySetError
        await self._check_password(user, password, client)
        return user
