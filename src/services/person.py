from uuid import UUID

from models.person import Person
from services.base import BaseService, Pagination
from storage.base import SearchField, Sort, TextQuery


class PersonService(BaseService[Person]):
    index = 'persons'
    model = Person

    async def get_by_id(self, person_id: UUID) -> Person | None:
        """Персона по id или None, если её нет."""
        return await self._get_by_id(person_id)

    async def get_list(self, pagination: Pagination) -> list[Person]:
        """Список персон в алфавитном порядке."""
        return await self._search(Person, pagination, sort=(Sort('full_name'), Sort('id')))

    async def search(self, query: str, pagination: Pagination) -> list[Person]:
        """Поиск персон по имени, сортировка по релевантности."""
        return await self._search(Person, pagination, text=TextQuery(query, (SearchField('full_name'),)))
