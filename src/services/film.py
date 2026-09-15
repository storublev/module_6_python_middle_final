from uuid import UUID

from models.film import Film, FilmShort
from services.base import BaseService, Pagination, sort_by
from storage.base import RelatedTo, SearchField, TextQuery

PERSON_ROLES = ('actors', 'writers', 'directors')


class FilmService(BaseService[Film]):
    index = 'movies'
    model = Film

    async def get_list(
        self,
        pagination: Pagination,
        sort: str,
        genre_id: UUID | None = None,
    ) -> list[FilmShort]:
        """Список фильмов с сортировкой и необязательным фильтром по жанру."""
        related_to = RelatedTo(str(genre_id), ('genres',)) if genre_id else None
        return await self._search(FilmShort, pagination, related_to=related_to, sort=sort_by(sort))

    async def search(self, query: str, pagination: Pagination) -> list[FilmShort]:
        """Полнотекстовый поиск по названию и описанию, сортировка по релевантности."""
        text = TextQuery(query, (SearchField('title', weight=3), SearchField('description')))
        return await self._search(FilmShort, pagination, text=text)

    async def get_by_person(self, person_id: UUID, pagination: Pagination, sort: str) -> list[FilmShort]:
        """Фильмы, в которых персона была актёром, сценаристом или режиссёром."""
        related_to = RelatedTo(str(person_id), PERSON_ROLES)
        return await self._search(FilmShort, pagination, related_to=related_to, sort=sort_by(sort))
