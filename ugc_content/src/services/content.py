"""Бизнес-логика пользовательского контента.

Слой знает правила («удалить рецензию может только автор», «у зрителя одна
оценка на фильм») и не знает ни про HTTP, ни про MongoDB: он работает с
интерфейсами из `storage/base.py`, поэтому в unit-тестах хранилище подменяется
реализацией в памяти, а в бою — MongoDB.

Логики здесь намеренно немного: большая часть правил выражена ограничениями
самого хранилища (уникальные индексы, upsert), потому что проверять их в коде
отдельным запросом — это гонка: между проверкой и записью успевает пройти
второй запрос того же зрителя.
"""

from uuid import UUID

from models.content import Bookmark, FilmRating, Like, Page, Review, ReviewSort
from services.errors import (
    BookmarkNotFoundError,
    NotReviewAuthorError,
    RatingNotFoundError,
    ReviewAlreadyExistsError,
    ReviewNotFoundError,
)
from storage.base import BookmarkStorage, LikeStorage, ReviewStorage


class LikeService:
    """Оценки фильмов и агрегаты по ним."""

    def __init__(self, storage: LikeStorage) -> None:
        self._storage = storage

    async def rate(self, film_id: UUID, user_id: UUID, rating: int) -> Like:
        """Ставит оценку от имени зрителя из токена."""
        return await self._storage.set_rating(film_id, user_id, rating)

    async def unrate(self, film_id: UUID, user_id: UUID) -> None:
        """Снимает оценку зрителя.

        Raises:
            RatingNotFoundError: зритель этот фильм не оценивал.
        """
        if not await self._storage.remove_rating(film_id, user_id):
            raise RatingNotFoundError

    async def my_rating(self, film_id: UUID, user_id: UUID) -> Like:
        """Отдаёт собственную оценку зрителя.

        Raises:
            RatingNotFoundError: оценки нет.
        """
        like = await self._storage.get_rating(film_id, user_id)
        if like is None:
            raise RatingNotFoundError
        return like

    async def film_rating(self, film_id: UUID) -> FilmRating:
        """Отдаёт лайки, дизлайки и среднюю оценку фильма."""
        return await self._storage.get_film_rating(film_id)

    async def liked_films(self, user_id: UUID, page: int, size: int) -> Page[Like]:
        """Отдаёт понравившиеся зрителю фильмы."""
        return await self._storage.list_liked_films(user_id, page, size)


class ReviewService:
    """Рецензии и голоса за их полезность."""

    def __init__(self, storage: ReviewStorage) -> None:
        self._storage = storage

    async def publish(self, film_id: UUID, user_id: UUID, text: str, rating: int | None) -> Review:
        """Публикует рецензию.

        Raises:
            ReviewAlreadyExistsError: у зрителя уже есть рецензия на этот фильм.
        """
        review = await self._storage.add_review(film_id, user_id, text, rating)
        if review is None:
            raise ReviewAlreadyExistsError
        return review

    async def withdraw(self, review_id: UUID, user_id: UUID) -> None:
        """Удаляет собственную рецензию зрителя.

        Авторство проверяется до удаления, а не ограничением хранилища:
        «рецензии нет» и «рецензия чужая» — разные ответы, и клиент должен их
        различать.

        Raises:
            ReviewNotFoundError: рецензии нет.
            NotReviewAuthorError: рецензия принадлежит другому зрителю.
        """
        review = await self._storage.get_review(review_id)
        if review is None:
            raise ReviewNotFoundError
        if review.user_id != user_id:
            raise NotReviewAuthorError
        if not await self._storage.delete_review(review_id):
            # Кто-то удалил её между проверкой и удалением — для клиента это
            # то же самое, что «её нет».
            raise ReviewNotFoundError

    async def vote(self, review_id: UUID, user_id: UUID, useful: bool) -> Review:
        """Отмечает рецензию полезной или бесполезной.

        Raises:
            ReviewNotFoundError: рецензии нет.
        """
        review = await self._storage.vote(review_id, user_id, useful)
        if review is None:
            raise ReviewNotFoundError
        return review

    async def film_reviews(self, film_id: UUID, sort: ReviewSort, page: int, size: int) -> Page[Review]:
        """Отдаёт рецензии фильма в выбранном порядке."""
        return await self._storage.list_reviews(film_id, sort, page, size)


class BookmarkService:
    """Отложенные фильмы."""

    def __init__(self, storage: BookmarkStorage) -> None:
        self._storage = storage

    async def add(self, film_id: UUID, user_id: UUID) -> Bookmark:
        """Откладывает фильм на потом; повторный вызов ничего не меняет."""
        return await self._storage.add(film_id, user_id)

    async def remove(self, film_id: UUID, user_id: UUID) -> None:
        """Убирает фильм из закладок.

        Raises:
            BookmarkNotFoundError: фильма не было в закладках.
        """
        if not await self._storage.remove(film_id, user_id):
            raise BookmarkNotFoundError

    async def list_for_user(self, user_id: UUID, page: int, size: int) -> Page[Bookmark]:
        """Отдаёт закладки зрителя в порядке добавления."""
        return await self._storage.list_for_user(user_id, page, size)
