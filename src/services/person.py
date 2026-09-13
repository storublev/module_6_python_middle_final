from models.person import Person
from services.base import BaseService, Pagination
from storage.base import TextQuery


class PersonService(BaseService[Person]):
    index = 'persons'
    model = Person

    async def search(self, query: str, pagination: Pagination) -> list[Person]:
        """Поиск персон по имени, сортировка по релевантности."""
        return await self._search(Person, pagination, text=TextQuery(query, ('full_name',)))
