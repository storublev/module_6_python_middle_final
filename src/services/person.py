from models.person import Person
from services.base import BaseService, Pagination
from storage.base import Sort, TextQuery


class PersonService(BaseService[Person]):
    index = 'persons'
    model = Person

    async def get_list(self, pagination: Pagination) -> list[Person]:
        """Список персон в алфавитном порядке."""
        return await self._search(Person, pagination, sort=(Sort('full_name'), Sort('id')))

    async def search(self, query: str, pagination: Pagination) -> list[Person]:
        """Поиск персон по имени, сортировка по релевантности."""
        return await self._search(Person, pagination, text=TextQuery(query, ('full_name',)))
