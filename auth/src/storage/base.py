"""Интерфейсы хранилищ сервиса авторизации.

Сервисы работают только с этими интерфейсами и не знают ни о PostgreSQL и
SQLAlchemy, ни о Redis. Реализации выбираются в api/dependencies.py.

Общий контракт: при сбое своего хранилища (нет соединения, истёк таймаут)
метод поднимает StorageUnavailableError — ошибки конкретной библиотеки
наружу не выходят.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from models.role import Role, UserAccess
from models.session import Session
from models.social import SocialAccount, SocialProfile
from models.user import LoginRecord, User


class StorageUnavailableError(Exception):
    """Хранилище не может ответить: нет соединения или истёк таймаут."""


class AlreadyExistsError(Exception):
    """Запись с таким уникальным значением уже есть."""


class UserRepository(ABC):
    """Учётные записи и то, какие у пользователя роли и права."""

    @abstractmethod
    async def get(self, user_id: UUID) -> User | None:
        """Возвращает пользователя или None, если его нет."""

    @abstractmethod
    async def get_by_login(self, login: str) -> User | None:
        """Возвращает пользователя по логину или None, если его нет."""

    @abstractmethod
    async def create(self, login: str, password_hash: str, is_superuser: bool = False) -> User:
        """Создаёт пользователя.

        Raises:
            AlreadyExistsError: логин занят.
        """

    @abstractmethod
    async def update_login(self, user_id: UUID, login: str) -> User:
        """Меняет логин.

        Raises:
            AlreadyExistsError: логин занят.
        """

    @abstractmethod
    async def update_password(self, user_id: UUID, password_hash: str) -> int:
        """Меняет хеш пароля и в той же транзакции увеличивает версию учётных данных; возвращает новую версию."""

    @abstractmethod
    async def get_credentials_version(self, user_id: UUID) -> int | None:
        """Возвращает текущую версию учётных данных или None, если пользователя нет."""

    @abstractmethod
    async def get_access(self, user_id: UUID) -> UserAccess | None:
        """Возвращает признак суперпользователя, роли и права или None, если пользователя нет."""


class RoleRepository(ABC):
    """Роли и их назначение пользователям.

    Каждое изменение, влияющее на права, в той же транзакции записывает
    задание на сброс кеша прав (AccessInvalidationQueue): изменение и задание
    фиксируются вместе или не фиксируются вовсе.
    """

    @abstractmethod
    async def get_all(self) -> list[Role]:
        """Возвращает все роли по имени."""

    @abstractmethod
    async def get(self, role_id: UUID) -> Role | None:
        """Возвращает роль или None, если её нет."""

    @abstractmethod
    async def create(self, name: str, description: str | None, permissions: list[str]) -> Role:
        """Создаёт роль.

        Raises:
            AlreadyExistsError: роль с таким именем уже есть.
        """

    @abstractmethod
    async def update(self, role_id: UUID, changes: dict[str, Any]) -> Role | None:
        """Меняет поля роли (name, description, permissions); None — роли нет.

        Raises:
            AlreadyExistsError: роль с таким именем уже есть.
        """

    @abstractmethod
    async def delete(self, role_id: UUID) -> bool:
        """Удаляет роль вместе с её назначениями; False — роли не было."""

    @abstractmethod
    async def list_for_user(self, user_id: UUID) -> list[Role]:
        """Возвращает роли пользователя по имени."""

    @abstractmethod
    async def assign(self, user_id: UUID, role_id: UUID) -> None:
        """Назначает роль пользователю; повторное назначение ничего не меняет."""

    @abstractmethod
    async def revoke(self, user_id: UUID, role_id: UUID) -> bool:
        """Отбирает роль у пользователя; False — роль не была назначена."""


@dataclass(frozen=True)
class AccessInvalidation:
    """Задание на сброс кеша прав: одного пользователя или всех (user_id=None)."""

    id: int
    user_id: UUID | None


class AccessInvalidationQueue(ABC):
    """Задания на сброс кеша прав, записанные вместе с изменением ролей."""

    @abstractmethod
    async def process(
        self, handler: Callable[[list[AccessInvalidation]], Awaitable[None]], limit: int,
    ) -> int:
        """Передаёт обработчику до limit заданий и удаляет их, если он завершился без ошибки.

        Задания, которые в это время выполняет другой процесс, пропускаются.
        Если обработчик поднял исключение, задания остаются и будут выполнены
        при следующем вызове. Возвращает, сколько заданий выполнено.
        """


class LoginHistoryRepository(ABC):
    """История входов в аккаунт."""

    @abstractmethod
    async def add(self, user_id: UUID, user_agent: str | None, ip: str | None) -> None:
        """Записывает вход."""

    @abstractmethod
    async def get_page(self, user_id: UUID, offset: int, limit: int) -> list[LoginRecord]:
        """Возвращает страницу входов пользователя, от новых к старым."""


class SocialAccountRepository(ABC):
    """Связи учётных записей с аккаунтами в соцсетях."""

    @abstractmethod
    async def get_user(self, provider: str, social_id: str) -> User | None:
        """Возвращает владельца аккаунта соцсети или None, если он ни к кому не привязан."""

    @abstractmethod
    async def create_user(self, login: str, provider: str, profile: SocialProfile) -> User:
        """Заводит учётную запись без пароля и сразу привязывает к ней аккаунт соцсети.

        Обе записи появляются в одной транзакции: учётная запись без связи
        осталась бы недоступной — войти в неё нечем.

        Raises:
            AlreadyExistsError: логин занят или аккаунт соцсети уже привязан.
        """

    @abstractmethod
    async def link(self, user_id: UUID, provider: str, profile: SocialProfile) -> SocialAccount:
        """Привязывает аккаунт соцсети к существующей учётной записи.

        Raises:
            AlreadyExistsError: аккаунт уже привязан или у пользователя уже есть аккаунт этой соцсети.
        """

    @abstractmethod
    async def list_for_user(self, user_id: UUID) -> list[SocialAccount]:
        """Возвращает привязанные аккаунты пользователя по имени поставщика."""

    @abstractmethod
    async def unlink(self, user_id: UUID, provider: str) -> bool:
        """Открепляет аккаунт; False — такого аккаунта у пользователя не было."""


class ProviderRejectedError(Exception):
    """Поставщик OAuth не принял запрос: код просрочен, уже использован или не наш."""


class ProviderUnavailableError(Exception):
    """Поставщик OAuth не ответил: сбой сети, таймаут или ошибка на его стороне."""


class OAuthProvider(ABC):
    """Соцсеть, через которую можно войти, — со стороны потребителя."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Короткое имя в адресах: yandex, google."""

    @property
    @abstractmethod
    def title(self) -> str:
        """Название для человека."""

    @abstractmethod
    async def authorization_url(self, state: str, redirect_uri: str) -> str:
        """Адрес поставщика, куда отправляется пользователь."""

    @abstractmethod
    async def fetch_profile(self, code: str, redirect_uri: str) -> SocialProfile:
        """Меняет код на токен и читает им данные пользователя.

        Raises:
            ProviderRejectedError: поставщик не принял код.
            ProviderUnavailableError: поставщик не ответил.
        """


class OAuthStateStore(ABC):
    """Состояние начатых входов через соцсеть (параметр state).

    state связывает переход к поставщику с возвратом от него: без него чужой
    ответ поставщика привязал бы к сессии жертвы чужой аккаунт (CSRF).
    Поэтому state одноразовый — прочитанное значение сразу исчезает — и живёт
    недолго: незавершённый вход не должен ждать вечно.
    """

    @abstractmethod
    async def save(self, state: str, payload: str, ttl: timedelta) -> None:
        """Запоминает начатый вход."""

    @abstractmethod
    async def pop(self, state: str) -> str | None:
        """Читает и сразу удаляет состояние; None — его нет или оно уже использовано."""


class RotateResult(StrEnum):
    """Чем закончилась замена refresh-токена сессии."""

    ROTATED = 'rotated'
    # Предъявлен уже использованный refresh-токен: его могли украсть.
    REUSED = 'reused'
    # Сессии нет: пользователь вышел или она истекла.
    MISSING = 'missing'


class SessionStore(ABC):
    """Активные сессии пользователей.

    access-токены не хранятся: токен действителен, пока жива его сессия.
    Поэтому выход и «выйти из остальных устройств» удаляют сессии, а не
    записывают токены в чёрный список.
    """

    @abstractmethod
    async def create(self, session: Session, ttl: timedelta) -> None:
        """Сохраняет сессию на время жизни refresh-токена.

        Если сессий у пользователя становится больше предела, закрываются те,
        что дольше всех не продлевались.
        """

    @abstractmethod
    async def get(self, session_id: UUID) -> Session | None:
        """Возвращает сессию или None, если она закрыта или истекла."""

    @abstractmethod
    async def set_credentials_version(self, session_id: UUID, version: int) -> None:
        """Переводит живую сессию на новую версию учётных данных; закрытую не воскрешает."""

    @abstractmethod
    async def rotate(
        self, user_id: UUID, session_id: UUID, old_jti: str, new_jti: str, ttl: timedelta,
    ) -> RotateResult:
        """Атомарно заменяет refresh-токен сессии, если предъявлен действующий, и продлевает её."""

    @abstractmethod
    async def delete(self, user_id: UUID, session_id: UUID) -> None:
        """Удаляет сессию."""

    @abstractmethod
    async def delete_others(self, user_id: UUID, keep_session_id: UUID) -> int:
        """Удаляет все сессии пользователя, кроме указанной; возвращает, сколько удалено."""


class AccessCache(ABC):
    """Кеш прав пользователей для быстрой проверки доступа.

    Запись сохраняется с отметкой версии, полученной при чтении. Если между
    чтением из кеша и записью в него права изменились, запись со старой
    отметкой больше не выдаётся — устаревшие права не попадут в кеш даже
    при гонке с назначением роли.
    """

    @abstractmethod
    async def get(self, user_id: UUID) -> tuple[UserAccess | None, str]:
        """Возвращает права из кеша (None — промах) и отметку версии для записи."""

    @abstractmethod
    async def set(self, user_id: UUID, access: UserAccess, version: str, ttl: timedelta) -> None:
        """Сохраняет права с отметкой версии, полученной в get()."""

    @abstractmethod
    async def invalidate_user(self, user_id: UUID) -> None:
        """Сбрасывает права одного пользователя: ему назначили или отобрали роль."""

    @abstractmethod
    async def invalidate_all(self) -> None:
        """Сбрасывает права всех пользователей: изменили или удалили роль."""


@dataclass(frozen=True)
class RateLimit:
    """Не больше limit попыток с одним ключом за скользящее окно period."""

    key: str
    limit: int
    period: timedelta


class RateLimiter(ABC):
    """Счётчики попыток, общие для всех процессов и реплик сервиса.

    Окно скользящее: считаются попытки за последние period, а не с начала
    минуты или часа, поэтому на стыке окон лимит не удваивается.
    """

    @abstractmethod
    async def acquire(self, limits: Sequence[RateLimit]) -> timedelta | None:
        """Засчитывает попытку сразу во всех лимитах.

        Если хотя бы один лимит исчерпан, попытка не засчитывается ни в один
        из них, и возвращается, через сколько освободится место; иначе — None.
        """

    @abstractmethod
    async def reset(self, key: str) -> None:
        """Обнуляет счётчик попыток с ключом."""
