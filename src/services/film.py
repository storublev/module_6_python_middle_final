from uuid import UUID

from models.film import Film, FilmShort
from services.access import Access
from services.base import BaseService, Pagination, sort_by
from services.errors import AccessCheckUnavailableError, SubscriptionRequiredError
from storage.base import FieldIn, RelatedTo, SearchField, TextQuery

PERSON_ROLES = ('actors', 'writers', 'directors')
ACCESS_LEVEL_FIELD = 'access_level'


class FilmService(BaseService[Film]):
    index = 'movies'
    model = Film

    async def get_by_id(self, film_id: UUID, access: Access) -> Film | None:
        """Фильм по id, если он открыт пользователю.

        Raises:
            SubscriptionRequiredError: фильм только по подписке, а её нет.
            AccessCheckUnavailableError: права не проверить, сервис авторизации молчит.
        """
        film = await super().get_by_id(film_id)
        if film is None or access.allows(film.access_level):
            return film
        # Отказ по подписке и невозможность её проверить — разные ответы:
        # во втором случае запрос стоит повторить позже.
        if access.degraded:
            raise AccessCheckUnavailableError
        raise SubscriptionRequiredError

    async def get_list(
        self,
        pagination: Pagination,
        access: Access,
        sort: str,
        genre_id: UUID | None = None,
    ) -> list[FilmShort]:
        """Список фильмов с сортировкой и необязательным фильтром по жанру."""
        related_to = RelatedTo(str(genre_id), ('genres',)) if genre_id else None
        return await self._search(
            FilmShort, pagination, related_to=related_to, filters=self._visible(access), sort=sort_by(sort),
        )

    async def search(self, query: str, pagination: Pagination, access: Access) -> list[FilmShort]:
        """Полнотекстовый поиск по названию и описанию, сортировка по релевантности."""
        text = TextQuery(query, (SearchField('title', weight=3), SearchField('description')))
        return await self._search(FilmShort, pagination, text=text, filters=self._visible(access))

    async def get_by_person(
        self,
        person_id: UUID,
        pagination: Pagination,
        access: Access,
        sort: str,
    ) -> list[FilmShort]:
        """Фильмы, в которых персона была актёром, сценаристом или режиссёром."""
        related_to = RelatedTo(str(person_id), PERSON_ROLES)
        return await self._search(
            FilmShort, pagination, related_to=related_to, filters=self._visible(access), sort=sort_by(sort),
        )

    @staticmethod
    def _visible(access: Access) -> tuple[FieldIn, ...]:
        """Ограничение выдачи уровнями доступа, открытыми пользователю.

        Закрытые фильмы в списках не показываются вовсе, а не отдаются с
        отказом: иначе страницы получались бы разной длины, а пагинация
        считала бы то, чего пользователь не увидит. Отбор входит в ключ кеша,
        поэтому выдача анонимного и подписчика не смешивается.
        """
        return (FieldIn(ACCESS_LEVEL_FIELD, access.levels),)
