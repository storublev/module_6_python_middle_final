"""Справочник пользователей для сервиса уведомлений.

Воркеру уведомлений приходит только `user_id`, а письмо собрать нужно с
адресом и именем — за ними он идёт сюда. Отдельный сервис, а не расширение
личного кабинета, потому что это другой контракт с другими правилами: тут
читают чужие данные пачкой и по служебному токену, а не свои по access-токену.

Наружу отдаётся `Contact` — минимальный набор бизнес-данных, а не строка
таблицы. Так учит урок про отчётные события: контракт не должен повторять
схему базы, иначе её нельзя будет менять, не ломая потребителей.
"""

import logging
from collections.abc import Sequence
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models.user import Contact, ProfileUpdate, User
from services.errors import UnknownTimezoneError
from storage.base import UserRepository

logger = logging.getLogger(__name__)

# Страница обхода всех пользователей. Больше тысячи за раз не нужно: письма
# всё равно собираются пачками такого же размера.
MAX_PAGE_SIZE = 1000


class DirectoryService:
    """Чтение контактов пользователей и правка собственного профиля."""

    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def contacts(self, user_ids: Sequence[UUID]) -> list[Contact]:
        """Контакты перечисленных пользователей одним запросом.

        Порядок ответа не совпадает с порядком запроса, а ненайденные
        пользователи в нём просто отсутствуют: потребитель сопоставляет по
        идентификатору и сам решает, что делать с теми, кого нет.
        """
        # Повторы в запросе не должны превращаться в повторы в ответе:
        # потребитель считает по ним письма.
        unique = list(dict.fromkeys(user_ids))
        return await self._users.contacts_by_ids(unique)

    async def page(self, after_id: UUID | None, limit: int) -> list[Contact]:
        """Страница контактов для рассылки всем: листание по ключу, не по смещению."""
        return await self._users.contacts_page(after_id, min(limit, MAX_PAGE_SIZE))

    async def update_profile(self, user_id: UUID, changes: ProfileUpdate) -> User:
        """Меняет контакты и имя владельца учётной записи.

        Raises:
            UnknownTimezoneError: такого часового пояса нет в базе IANA.
        """
        if changes.timezone:
            self._check_timezone(changes.timezone)
        return await self._users.update_profile(user_id, changes)

    @staticmethod
    def _check_timezone(name: str) -> None:
        # Проверять обязательно: по этому значению сервис уведомлений решает,
        # когда писать. Неизвестный пояс превратился бы в ошибку у воркера,
        # то есть в неотправленное письмо, а не в понятный отказ здесь.
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise UnknownTimezoneError from error
