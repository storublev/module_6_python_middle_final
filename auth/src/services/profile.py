from dataclasses import dataclass

from models.role import Role
from models.user import LoginRecord, User
from services.auth import Principal
from services.errors import LoginTakenError, TokenRevokedError, WrongPasswordError
from services.passwords import PasswordHasher
from storage.base import AlreadyExistsError, LoginHistoryRepository, RoleRepository, SessionStore, UserRepository


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

    async def change_password(self, principal: Principal, password: str, new_password: str) -> None:
        """Меняет пароль и закрывает остальные сессии: если пароль узнал кто-то ещё, он потеряет доступ.

        Raises:
            WrongPasswordError: текущий пароль неверный.
        """
        user = await self._check_password(principal, password)
        await self.users.update_password(user.id, await self.passwords.hash(new_password))
        await self.sessions.delete_others(user.id, keep_session_id=principal.session_id)

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
        if not await self.passwords.verify(password, user.password_hash):
            raise WrongPasswordError
        return user
