import logging
from dataclasses import dataclass

from models.role import Role
from models.user import LoginRecord, User
from services.auth import Principal
from services.errors import (
    LoginTakenError,
    PasswordAlreadySetError,
    TokenRevokedError,
    WrongPasswordError,
)
from services.passwords import PasswordHasher
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
    ):
        self.users = users
        self.roles = roles
        self.history = history
        self.sessions = sessions
        self.passwords = passwords

    async def get_profile(self, principal: Principal) -> Profile:
        user = await self._get_user(principal)
        return Profile(user=user, roles=await self.roles.list_for_user(user.id))

    async def change_login(self, principal: Principal, new_login: str, password: str) -> User:
        """Меняет логин. Смену подтверждает текущий пароль: одного украденного токена мало.

        Raises:
            WrongPasswordError: текущий пароль неверный.
            LoginTakenError: логин занят другим пользователем.
        """
        user = await self._check_password(principal, password)
        if new_login == user.login:
            return user
        try:
            return await self.users.update_login(user.id, new_login)
        except AlreadyExistsError as exc:
            raise LoginTakenError from exc

    async def change_password(self, principal: Principal, password: str | None, new_password: str) -> None:
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
            WrongPasswordError: текущий пароль неверный.
            PasswordAlreadySetError: пароль уже есть, но текущий не передан.
        """
        user = await self._authorize_password_change(principal, password)
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

    async def _check_password(self, principal: Principal, password: str) -> User:
        user = await self._get_user(principal)
        # Пароля нет — подтверждать им нечего, и проверка не пройдена.
        if not user.has_password or not await self.passwords.verify(password, user.password_hash):
            raise WrongPasswordError
        return user

    async def _authorize_password_change(self, principal: Principal, password: str | None) -> User:
        user = await self._get_user(principal)
        if not user.has_password:
            # Первый пароль пользователя из соцсети: подтверждать нечем.
            return user
        if password is None:
            raise PasswordAlreadySetError
        if not await self.passwords.verify(password, user.password_hash):
            raise WrongPasswordError
        return user
