"""Интерфейсы хранилища пользовательского контента.

Бизнес-логика знает, что оценки, рецензии и закладки где-то лежат, и не знает,
что это MongoDB: в unit-тестах на место этих интерфейсов встают реализации в
памяти, а хранилище можно поменять, не трогая слой `services`. Так же устроены
Async API и сервис сбора событий.

Контракт для всех реализаций: сбой хранилища выходит наружу как
`StorageUnavailableError`, а не как исключение драйвера. API отвечает на него
503 и пишет причину в журнал. Нарушение бизнес-правила (нет такой рецензии,
повторная закладка) — это не сбой, и исключением не выражается: методы
возвращают результат, по которому видно, что произошло.
"""

from abc import ABC, abstractmethod
from uuid import UUID

from models.content import Bookmark, FilmRating, Like, Page, Review, ReviewSort


class StorageUnavailableError(Exception):
    """Хранилище недоступно: нет соединения, таймаут, отказ записи."""


class LikeStorage(ABC):
    """Оценки фильмов."""

    @abstractmethod
    async def set_rating(self, film_id: UUID, user_id: UUID, rating: int) -> Like:
        """Ставит или меняет оценку зрителя. Повторный вызов заменяет прежнюю."""

    @abstractmethod
    async def remove_rating(self, film_id: UUID, user_id: UUID) -> bool:
        """Снимает оценку. Возвращает False, если оценки не было."""

    @abstractmethod
    async def get_film_rating(self, film_id: UUID) -> FilmRating:
        """Считает агрегат по фильму: лайки, дизлайки и среднюю оценку.

        Самый горячий запрос сервиса — он выполняется при каждом открытии
        карточки фильма и обязан укладываться в 200 мс.
        """

    @abstractmethod
    async def list_liked_films(self, user_id: UUID, page: int, size: int) -> Page[Like]:
        """Отдаёт страницу фильмов, которые зритель оценил положительно."""


class ReviewStorage(ABC):
    """Рецензии и голоса за их полезность."""

    @abstractmethod
    async def add_review(self, film_id: UUID, user_id: UUID, text: str, rating: int | None) -> Review:
        """Публикует рецензию зрителя на фильм."""

    @abstractmethod
    async def delete_review(self, review_id: UUID, user_id: UUID) -> bool:
        """Удаляет рецензию её автора. Возвращает False, если рецензии нет."""

    @abstractmethod
    async def vote(self, review_id: UUID, user_id: UUID, useful: bool) -> Review | None:
        """Голосует за полезность рецензии; None — рецензии не существует."""

    @abstractmethod
    async def list_reviews(self, film_id: UUID, sort: ReviewSort, page: int, size: int) -> Page[Review]:
        """Отдаёт страницу рецензий фильма в заданном порядке."""


class BookmarkStorage(ABC):
    """Отложенные фильмы."""

    @abstractmethod
    async def add(self, film_id: UUID, user_id: UUID) -> Bookmark:
        """Добавляет фильм в закладки. Повторный вызов ничего не меняет."""

    @abstractmethod
    async def remove(self, film_id: UUID, user_id: UUID) -> bool:
        """Убирает фильм из закладок. Возвращает False, если закладки не было."""

    @abstractmethod
    async def list_for_user(self, user_id: UUID, page: int, size: int) -> Page[Bookmark]:
        """Отдаёт страницу закладок зрителя в порядке добавления."""


class HealthCheck(ABC):
    """Готовность хранилища — для проверки готовности контейнера."""

    @abstractmethod
    async def is_ready(self) -> bool:
        """Отвечает ли хранилище на запросы."""
