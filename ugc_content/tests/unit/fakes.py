"""Хранилища в памяти: бизнес-логика проверяется без Docker и без MongoDB.

Это не заглушки-пустышки, а рабочие реализации тех же интерфейсов: они держат
те же правила, что и MongoDB (одна оценка зрителя на фильм, одна рецензия на
фильм, один голос за рецензию), иначе тесты проверяли бы не то поведение,
которое будет в бою.

Отдельно есть `BrokenLikeStorage` — хранилище, которое всегда падает. Им
проверяется, что API отвечает 503, а не 500.
"""

from datetime import datetime, timezone
from typing import TypeVar
from uuid import UUID, uuid4

from models.content import Bookmark, FilmRating, Like, Page, Review, ReviewSort
from storage.base import (
    LIKED_FROM,
    BookmarkStorage,
    HealthCheck,
    LikeStorage,
    ReviewStorage,
    StorageUnavailableError,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Дженерик через TypeVar, а не синтаксисом PEP 695: тесты гоняются в CI и на
# Python 3.10, где `def _page[T](...)` не разбирается.
T = TypeVar('T')


def _page(items: list[T], page: int, size: int) -> Page[T]:
    start = (page - 1) * size
    return Page[T](items=items[start:start + size], total=len(items), page=page, size=size)


class InMemoryLikeStorage(LikeStorage):
    """Оценки в словаре по паре «фильм — зритель»."""

    def __init__(self) -> None:
        self.items: dict[tuple[UUID, UUID], Like] = {}

    async def set_rating(self, film_id: UUID, user_id: UUID, rating: int) -> Like:
        existing = self.items.get((film_id, user_id))
        like = Like(
            film_id=film_id,
            user_id=user_id,
            rating=rating,
            created_at=existing.created_at if existing else _now(),
            updated_at=_now(),
        )
        self.items[(film_id, user_id)] = like
        return like

    async def remove_rating(self, film_id: UUID, user_id: UUID) -> bool:
        return self.items.pop((film_id, user_id), None) is not None

    async def get_rating(self, film_id: UUID, user_id: UUID) -> Like | None:
        return self.items.get((film_id, user_id))

    async def get_film_rating(self, film_id: UUID) -> FilmRating:
        ratings = [like.rating for (film, _), like in self.items.items() if film == film_id]
        if not ratings:
            return FilmRating(film_id=film_id, likes=0, dislikes=0, average_rating=None)
        likes = sum(1 for rating in ratings if rating >= LIKED_FROM)
        return FilmRating(
            film_id=film_id,
            likes=likes,
            dislikes=len(ratings) - likes,
            average_rating=round(sum(ratings) / len(ratings), 2),
        )

    async def list_liked_films(self, user_id: UUID, page: int, size: int) -> Page[Like]:
        liked = [
            like for (_, user), like in self.items.items()
            if user == user_id and like.rating >= LIKED_FROM
        ]
        liked.sort(key=lambda like: like.created_at, reverse=True)
        return _page(liked, page, size)


class InMemoryBookmarkStorage(BookmarkStorage):
    """Закладки в словаре по паре «зритель — фильм»."""

    def __init__(self) -> None:
        self.items: dict[tuple[UUID, UUID], Bookmark] = {}

    async def add(self, film_id: UUID, user_id: UUID) -> Bookmark:
        # Повторное добавление не меняет время: операция идемпотентна.
        existing = self.items.get((user_id, film_id))
        if existing:
            return existing
        bookmark = Bookmark(film_id=film_id, user_id=user_id, created_at=_now())
        self.items[(user_id, film_id)] = bookmark
        return bookmark

    async def remove(self, film_id: UUID, user_id: UUID) -> bool:
        return self.items.pop((user_id, film_id), None) is not None

    async def list_for_user(self, user_id: UUID, page: int, size: int) -> Page[Bookmark]:
        items = [bookmark for (user, _), bookmark in self.items.items() if user == user_id]
        items.sort(key=lambda bookmark: bookmark.created_at)
        return _page(items, page, size)


class InMemoryReviewStorage(ReviewStorage):
    """Рецензии и голоса за них в двух словарях."""

    def __init__(self) -> None:
        self.items: dict[UUID, Review] = {}
        self.votes: dict[tuple[UUID, UUID], bool] = {}

    async def add_review(self, film_id: UUID, user_id: UUID, text: str, rating: int | None) -> Review | None:
        if any(review.film_id == film_id and review.user_id == user_id for review in self.items.values()):
            return None
        review = Review(
            review_id=uuid4(),
            film_id=film_id,
            user_id=user_id,
            text=text,
            rating=rating,
            useful=0,
            useless=0,
            created_at=_now(),
        )
        self.items[review.review_id] = review
        return review

    async def get_review(self, review_id: UUID) -> Review | None:
        return self.items.get(review_id)

    async def delete_review(self, review_id: UUID) -> bool:
        if self.items.pop(review_id, None) is None:
            return False
        for key in [key for key in self.votes if key[0] == review_id]:
            del self.votes[key]
        return True

    async def vote(self, review_id: UUID, user_id: UUID, useful: bool) -> Review | None:
        review = self.items.get(review_id)
        if review is None:
            return None
        previous = self.votes.get((review_id, user_id))
        self.votes[(review_id, user_id)] = useful
        if previous == useful:
            return review
        changed = review.model_copy(update=self._counters(review, previous, useful))
        self.items[review_id] = changed
        return changed

    async def list_reviews(self, film_id: UUID, sort: ReviewSort, page: int, size: int) -> Page[Review]:
        items = [review for review in self.items.values() if review.film_id == film_id]
        keys = {
            ReviewSort.NEWEST: (lambda review: review.created_at, True),
            ReviewSort.OLDEST: (lambda review: review.created_at, False),
            ReviewSort.MOST_USEFUL: (lambda review: review.useful, True),
            ReviewSort.HIGHEST_RATING: (lambda review: review.rating or 0, True),
        }
        key, reverse = keys[sort]
        items.sort(key=key, reverse=reverse)
        return _page(items, page, size)

    @staticmethod
    def _counters(review: Review, previous: bool | None, useful: bool) -> dict[str, int]:
        if previous is None:
            return {'useful': review.useful + 1} if useful else {'useless': review.useless + 1}
        if useful:
            return {'useful': review.useful + 1, 'useless': max(0, review.useless - 1)}
        return {'useful': max(0, review.useful - 1), 'useless': review.useless + 1}


class BrokenLikeStorage(InMemoryLikeStorage):
    """Хранилище, которое всегда недоступно: им проверяется ответ 503."""

    async def get_film_rating(self, film_id: UUID) -> FilmRating:
        raise StorageUnavailableError('MongoDB недоступна')

    async def set_rating(self, film_id: UUID, user_id: UUID, rating: int) -> Like:
        raise StorageUnavailableError('MongoDB недоступна')


class ReadyHealthCheck(HealthCheck):
    """Готовность, которой можно управлять из теста."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def is_ready(self) -> bool:
        return self.ready
