"""Бизнес-логика пользовательского контента.

Каркас девятого спринта: здесь описано, что делает каждая операция и почему,
а тела методов появятся вместе с реализацией хранилища (эпик E3 в
docs/planning-sprint9.md). Классы объявлены заранее, чтобы было видно, где
проходит граница слоёв: сервисы работают с интерфейсами из `storage.base` и
ничего не знают ни о MongoDB, ни о FastAPI.
"""

from uuid import UUID

from models.content import Bookmark, FilmRating, Like, Page, Review, ReviewSort
from storage.base import BookmarkStorage, LikeStorage, ReviewStorage


class LikeService:
    """Оценки фильмов и агрегаты по ним."""

    def __init__(self, storage: LikeStorage) -> None:
        self._storage = storage

    async def rate(self, film_id: UUID, user_id: UUID, rating: int) -> Like:
        """Ставит оценку от имени зрителя из токена."""
        raise NotImplementedError

    async def unrate(self, film_id: UUID, user_id: UUID) -> bool:
        """Снимает оценку зрителя."""
        raise NotImplementedError

    async def film_rating(self, film_id: UUID) -> FilmRating:
        """Отдаёт лайки, дизлайки и среднюю оценку фильма."""
        raise NotImplementedError

    async def liked_films(self, user_id: UUID, page: int, size: int) -> Page[Like]:
        """Отдаёт понравившиеся зрителю фильмы."""
        raise NotImplementedError


class ReviewService:
    """Рецензии и голоса за их полезность.

    Голос за полезность рецензии — та же механика, что и лайк фильма, но
    хранится рядом с рецензией: список рецензий сортируется по числу голосов,
    и собирать его соединением двух коллекций значило бы не уложиться в 200 мс.
    """

    def __init__(self, storage: ReviewStorage) -> None:
        self._storage = storage

    async def publish(self, film_id: UUID, user_id: UUID, text: str, rating: int | None) -> Review:
        """Публикует рецензию."""
        raise NotImplementedError

    async def withdraw(self, review_id: UUID, user_id: UUID) -> bool:
        """Удаляет собственную рецензию зрителя."""
        raise NotImplementedError

    async def vote(self, review_id: UUID, user_id: UUID, useful: bool) -> Review | None:
        """Отмечает рецензию полезной или бесполезной."""
        raise NotImplementedError

    async def film_reviews(self, film_id: UUID, sort: ReviewSort, page: int, size: int) -> Page[Review]:
        """Отдаёт рецензии фильма в выбранном порядке."""
        raise NotImplementedError


class BookmarkService:
    """Отложенные фильмы."""

    def __init__(self, storage: BookmarkStorage) -> None:
        self._storage = storage

    async def add(self, film_id: UUID, user_id: UUID) -> Bookmark:
        """Откладывает фильм на потом."""
        raise NotImplementedError

    async def remove(self, film_id: UUID, user_id: UUID) -> bool:
        """Убирает фильм из закладок."""
        raise NotImplementedError

    async def list_for_user(self, user_id: UUID, page: int, size: int) -> Page[Bookmark]:
        """Отдаёт закладки зрителя в порядке добавления."""
        raise NotImplementedError
