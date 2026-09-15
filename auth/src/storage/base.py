"""Интерфейсы хранилищ сервиса авторизации.

Сервисы работают только с этими интерфейсами и не знают ни о PostgreSQL и
SQLAlchemy, ни о Redis. Реализации выбираются в api/dependencies.py.

Общий контракт: при сбое своего хранилища (нет соединения, истёк таймаут)
метод поднимает StorageUnavailableError — ошибки конкретной библиотеки
наружу не выходят.
"""

from abc import ABC, abstractmethod
from datetime import timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from models.role import Role, UserAccess
from models.session import Session
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
    async def update_password(self, user_id: UUID, password_hash: str) -> None:
        """Меняет хеш пароля."""

    @abstractmethod
    async def get_access(self, user_id: UUID) -> UserAccess | None:
        """Возвращает признак суперпользователя, роли и права или None, если пользователя нет."""


class RoleRepository(ABC):
    """Роли и их назначение пользователям."""

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


class LoginHistoryRepository(ABC):
    """История входов в аккаунт."""

    @abstractmethod
    async def add(self, user_id: UUID, user_agent: str | None, ip: str | None) -> None:
        """Записывает вход."""

    @abstractmethod
    async def get_page(self, user_id: UUID, offset: int, limit: int) -> list[LoginRecord]:
        """Возвращает страницу входов пользователя, от новых к старым."""


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
        """Сохраняет сессию на время жизни refresh-токена."""

    @abstractmethod
    async def exists(self, session_id: UUID) -> bool:
        """Жива ли сессия."""

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
