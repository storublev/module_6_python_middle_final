"""Контракт пользовательского контента: лайки, рецензии и закладки.

Модели описывают, что сервис принимает и отдаёт. Как это ложится в коллекции
MongoDB — дело слоя `storage`: документ хранилища может отличаться от ответа
API (например, счётчики лайков рецензии считаются заранее, чтобы список
рецензий не собирался соединением нескольких коллекций).

Чего в моделях нет: `user_id` во входных данных. Его нельзя брать из тела
запроса — клиент прислал бы чужой. Сервис подставляет его из access-токена.
"""

from enum import StrEnum
from typing import Annotated, Generic, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Оценка фильма: 0 — дизлайк, 10 — лайк. Диапазон заложен сразу, даже если
# интерфейс покажет только «палец вверх/вниз»: поведение лайков определят
# аналитики позже, и менять из-за этого схему хранилища не придётся.
Rating = Annotated[int, Field(ge=0, le=10)]
ReviewText = Annotated[str, Field(min_length=1, max_length=10_000)]


class ReviewSort(StrEnum):
    """По чему сортируется список рецензий фильма.

    Сортировок несколько намеренно. Показывать всегда самые залайканные —
    ловушка: новые рецензии не наберут лайков и никогда не поднимутся. Какой
    порядок правильный, решат аналитики; хранилище должно уметь все.
    """

    NEWEST = 'newest'
    OLDEST = 'oldest'
    MOST_LIKED = 'most_liked'
    HIGHEST_RATING = 'highest_rating'


class LikeRequest(BaseModel):
    """Оценка фильма, которую ставит зритель."""

    model_config = ConfigDict(extra='forbid')

    rating: Rating


class Like(BaseModel):
    """Оценка фильма конкретным зрителем."""

    film_id: UUID
    user_id: UUID
    rating: Rating
    created_at: AwareDatetime
    updated_at: AwareDatetime


class FilmRating(BaseModel):
    """Агрегат по фильму — то, что показывается в карточке.

    Считается по всем оценкам фильма. Это самый горячий запрос сервиса: он
    выполняется при каждом открытии карточки, и именно он обязан укладываться
    в 200 мс.
    """

    film_id: UUID
    likes: int = Field(ge=0)
    dislikes: int = Field(ge=0)
    average_rating: float | None = Field(default=None, ge=0, le=10)


class ReviewRequest(BaseModel):
    """Рецензия, которую публикует зритель."""

    model_config = ConfigDict(extra='forbid')

    text: ReviewText
    # Оценка фильма, привязанная к рецензии: по заданию рецензия и оценка
    # связаны, но рецензию можно написать и не оценивая фильм.
    rating: Rating | None = None


class Review(BaseModel):
    """Рецензия на фильм вместе с голосами за неё."""

    review_id: UUID
    film_id: UUID
    user_id: UUID
    text: ReviewText
    rating: Rating | None = None
    likes: int = Field(default=0, ge=0)
    dislikes: int = Field(default=0, ge=0)
    created_at: AwareDatetime


class ReviewVoteRequest(BaseModel):
    """Голос за полезность рецензии."""

    model_config = ConfigDict(extra='forbid')

    useful: bool


class Bookmark(BaseModel):
    """Фильм, отложенный зрителем на потом.

    Порядок в списке — порядок добавления: закладок у зрителя немного, и
    сортировки здесь не нужны.
    """

    film_id: UUID
    user_id: UUID
    created_at: AwareDatetime


# Generic через TypeVar, а не синтаксисом PEP 695 (`class Page[T]`): CI гоняет
# код на Python 3.10 и 3.11, где такого синтаксиса ещё нет.
T = TypeVar('T')


class Page(BaseModel, Generic[T]):
    """Страница списка: сервис не отдаёт длинные списки целиком."""

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1)


__all__ = [
    'Bookmark',
    'FilmRating',
    'Like',
    'LikeRequest',
    'Page',
    'Rating',
    'Review',
    'ReviewRequest',
    'ReviewSort',
    'ReviewVoteRequest',
]
