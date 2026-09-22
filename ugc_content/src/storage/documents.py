"""Документы MongoDB: как данные лежат в хранилище.

Документы отделены от моделей API (`models/content.py`) намеренно. Форма
документа выбирается ради скорости чтения — например, голоса за рецензию
хранятся и отдельной коллекцией (чтобы повторный голос заменял прежний), и
счётчиками в самой рецензии (чтобы список сортировался без соединений). В
ответе API ничего этого быть не должно.

Индексы — главное в этом файле: без них требование «агрегат за 200 мс»
невыполнимо ни в одном хранилище. Их состав повторяет сценарии из требований:

| Сценарий | Индекс |
|---|---|
| Агрегат по фильму (ФТ-3) | готовый счётчик `film_ratings` по `film_id` |
| Оценка зрителя (ФТ-1) | `likes (film_id, user_id)`, он же уникальный ключ |
| Понравившиеся зрителю (ФТ-4) | `likes (user_id, rating, created_at)` |
| Закладки зрителя (ФТ-10) | `bookmarks (user_id, created_at)` |
| Рецензии фильма с сортировками (ФТ-8) | `reviews (film_id, created_at)`, `(film_id, useful)`, `(film_id, rating)` |
| Одна рецензия зрителя на фильм | уникальный `reviews (film_id, user_id)` |
| Один голос зрителя за рецензию (ФТ-7) | уникальный `votes (review_id, user_id)` |
"""

from datetime import datetime
from uuid import UUID, uuid4

import pymongo
from beanie import Document
from pydantic import Field

COLLECTION_LIKES = 'likes'
COLLECTION_FILM_RATINGS = 'film_ratings'
COLLECTION_BOOKMARKS = 'bookmarks'
COLLECTION_REVIEWS = 'reviews'
COLLECTION_VOTES = 'review_votes'


def utc_now() -> datetime:
    """Текущее время в UTC.

    MongoDB хранит даты без часового пояса и считает их UTC, поэтому все
    записи делаются в UTC — иначе после перезапуска в другом поясе история
    сдвинулась бы.
    """
    from datetime import UTC

    return datetime.now(UTC)


class LikeDocument(Document):
    """Оценка фильма зрителем."""

    film_id: UUID
    user_id: UUID
    rating: int
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = COLLECTION_LIKES
        indexes = [
            pymongo.IndexModel(
                [('film_id', pymongo.ASCENDING), ('user_id', pymongo.ASCENDING)],
                unique=True,
                name='like_film_user_unique',
            ),
            pymongo.IndexModel(
                [('user_id', pymongo.ASCENDING), ('rating', pymongo.DESCENDING),
                 ('created_at', pymongo.DESCENDING)],
                name='like_user_rating',
            ),
        ]


class FilmRatingDocument(Document):
    """Готовый агрегат по фильму: лайки, дизлайки и сумма оценок.

    Зачем он нужен. Считать агрегат конвейером на каждом открытии карточки
    нельзя: у популярного фильма сотни тысяч оценок, и на данных исследования
    такой подсчёт занимает секунды — при требовании в 200 мс (НФТ-2). Поэтому
    счётчик двигается на каждую оценку (`$inc`), а чтение карточки — это одна
    запись по ключу.

    Чем платим: счётчик и сама оценка пишутся двумя операциями, и на
    одиночном mongod между ними нет транзакции. Если процесс умрёт ровно
    между ними, счётчик разойдётся с оценками на единицу. Лечится это тем же
    пересчётом, что в исследовании (`build_counters`), и полностью уходит на
    наборе реплик, где две записи заворачиваются в транзакцию.

    Среднее хранится не числом, а парой «сумма и количество»: среднее от
    среднего не считается, а сумма складывается.
    """

    film_id: UUID
    likes: int = 0
    dislikes: int = 0
    sum_rating: int = 0
    votes: int = 0

    class Settings:
        name = COLLECTION_FILM_RATINGS
        indexes = [
            pymongo.IndexModel([('film_id', pymongo.ASCENDING)], unique=True, name='film_rating_unique'),
        ]


class BookmarkDocument(Document):
    """Фильм, отложенный зрителем на потом."""

    user_id: UUID
    film_id: UUID
    created_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = COLLECTION_BOOKMARKS
        indexes = [
            pymongo.IndexModel(
                [('user_id', pymongo.ASCENDING), ('film_id', pymongo.ASCENDING)],
                unique=True,
                name='bookmark_user_film_unique',
            ),
            pymongo.IndexModel(
                [('user_id', pymongo.ASCENDING), ('created_at', pymongo.ASCENDING)],
                name='bookmark_user_created',
            ),
        ]


class ReviewDocument(Document):
    """Рецензия на фильм.

    Счётчики `useful` и `useless` лежат прямо в документе, хотя сами голоса
    хранятся отдельной коллекцией. Это сознательная денормализация: список
    рецензий фильма сортируется по полезности, и считать её каждый раз по
    коллекции голосов означало бы агрегацию на каждый показ страницы.
    """

    review_id: UUID = Field(default_factory=uuid4)
    film_id: UUID
    user_id: UUID
    text: str
    rating: int | None = None
    useful: int = 0
    useless: int = 0
    created_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = COLLECTION_REVIEWS
        indexes = [
            pymongo.IndexModel([('review_id', pymongo.ASCENDING)], unique=True, name='review_id_unique'),
            pymongo.IndexModel(
                [('film_id', pymongo.ASCENDING), ('user_id', pymongo.ASCENDING)],
                unique=True,
                name='review_film_user_unique',
            ),
            pymongo.IndexModel(
                [('film_id', pymongo.ASCENDING), ('created_at', pymongo.DESCENDING)],
                name='review_film_created',
            ),
            pymongo.IndexModel(
                [('film_id', pymongo.ASCENDING), ('useful', pymongo.DESCENDING)],
                name='review_film_useful',
            ),
            pymongo.IndexModel(
                [('film_id', pymongo.ASCENDING), ('rating', pymongo.DESCENDING)],
                name='review_film_rating',
            ),
        ]


class ReviewVoteDocument(Document):
    """Голос зрителя за полезность рецензии."""

    review_id: UUID
    user_id: UUID
    useful: bool
    created_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = COLLECTION_VOTES
        indexes = [
            pymongo.IndexModel(
                [('review_id', pymongo.ASCENDING), ('user_id', pymongo.ASCENDING)],
                unique=True,
                name='vote_review_user_unique',
            ),
        ]


DOCUMENTS = [LikeDocument, FilmRatingDocument, BookmarkDocument, ReviewDocument, ReviewVoteDocument]
