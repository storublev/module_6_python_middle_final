"""Имена хоста и гостя для страниц и писем.

Имя берётся из справочника сервиса авторизации один раз — при создании показа
или брони — и дальше живёт снимком (ADR-22). Если справочник не ответил,
показ и бронь всё равно создаются: имя — украшение, а не условие брони, и
отказывать зрителю из-за него нельзя (НФТ-4).
"""

import logging
from uuid import UUID

from storage.base import People, StorageUnavailableError

logger = logging.getLogger(__name__)

# Подпись для зрителя без имени: справочник недоступен или имя не заполнено.
UNKNOWN_NAME = 'Зритель'


class NameResolver:
    def __init__(self, people: People) -> None:
        self._people = people

    async def name_of(self, user_id: UUID) -> str:
        try:
            names = await self._people.names([user_id])
        except StorageUnavailableError as error:
            logger.warning('Справочник имён недоступен, пишем «%s»: %s', UNKNOWN_NAME, error)
            return UNKNOWN_NAME
        return names.get(user_id) or UNKNOWN_NAME
