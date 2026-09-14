from models.genre import Genre
from services.base import BaseService, Pagination
from storage.base import Sort


class GenreService(BaseService[Genre]):
    index = 'genres'
    model = Genre

    async def get_list(self, pagination: Pagination) -> list[Genre]:
        """Список жанров в алфавитном порядке."""
        return await self._search(Genre, pagination, sort=(Sort('name'), Sort('id')))
