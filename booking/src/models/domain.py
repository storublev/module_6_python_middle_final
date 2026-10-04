"""Сущности бронирования: показ, бронь, оценка и то, что из них собирается.

Модели не знают ни о базе, ни о HTTP: их создают хранилища и читают сервисы.
Имена хоста и гостя, название и обложка фильма лежат в показе и брони
снимком на момент создания (ADR-22): страницы списков собираются без похода
в каталог и сервис авторизации на каждую строку.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScreeningStatus(str, Enum):
    SCHEDULED = 'scheduled'
    CANCELLED = 'cancelled'


class BookingStatus(str, Enum):
    ACTIVE = 'active'
    CANCELLED = 'cancelled'


class Role(str, Enum):
    """Кем был зритель на показе: от этого зависит, чей рейтинг двигает оценка."""

    HOST = 'host'
    GUEST = 'guest'


class Period(str, Enum):
    """Какие показы показывать: ещё не начавшиеся или уже прошедшие."""

    UPCOMING = 'upcoming'
    PAST = 'past'


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class Film(Model):
    """Фильм из каталога — ровно то, что нужно показу."""

    id: UUID
    title: str
    type: str | None = None
    poster_url: str | None = None


class ScreeningDraft(Model):
    """Новый показ, уже проверенный сервисом."""

    host_id: UUID
    host_name: str
    film_id: UUID
    film_title: str
    film_poster: str | None
    starts_at: datetime
    place: str
    address: str
    description: str | None
    capacity: int


class ScreeningChanges(Model):
    """Что хост меняет в показе. None — поле не меняется."""

    starts_at: datetime | None = None
    place: str | None = None
    address: str | None = None
    description: str | None = None
    capacity: int | None = None

    @property
    def is_empty(self) -> bool:
        return all(value is None for value in self.model_dump().values())

    @property
    def concerns_guests(self) -> bool:
        """Изменение, о котором надо написать гостям: они придут не туда или не тогда."""
        return any(value is not None for value in (self.starts_at, self.place, self.address))


class Screening(Model):
    id: UUID
    host_id: UUID
    host_name: str
    film_id: UUID
    film_title: str
    film_poster: str | None
    starts_at: datetime
    place: str
    address: str
    description: str | None
    capacity: int
    seats_taken: int
    status: ScreeningStatus
    created_at: datetime
    updated_at: datetime

    @property
    def seats_left(self) -> int:
        return self.capacity - self.seats_taken

    def is_open(self, now: datetime) -> bool:
        """Можно ли ещё бронировать и менять показ."""
        return self.status is ScreeningStatus.SCHEDULED and self.starts_at > now


class BookingDraft(Model):
    screening_id: UUID
    guest_id: UUID
    guest_name: str
    seats: int


class Booking(Model):
    id: UUID
    screening_id: UUID
    guest_id: UUID
    guest_name: str
    seats: int
    status: BookingStatus
    created_at: datetime
    updated_at: datetime


class RatingSummary(Model):
    """Рейтинг зрителя в одной роли: средняя оценка и сколько их."""

    average: float | None = None
    votes: int = 0


class UserRating(Model):
    """Рейтинг зрителя как хоста и как гостя (ФТ-19)."""

    user_id: UUID
    name: str | None = None
    as_host: RatingSummary = RatingSummary()
    as_guest: RatingSummary = RatingSummary()


class GuestEntry(Model):
    """Строка списка гостей у хоста: бронь и рейтинг гостя."""

    booking: Booking
    rating: RatingSummary


class BookingView(Model):
    """Бронь гостя вместе с показом — строка страницы «Мои брони»."""

    booking: Booking
    screening: Screening


class HostOffer(Model):
    """Хост, предлагающий фильм: строка блока «Кто показывает» в карточке фильма."""

    host_id: UUID
    host_name: str
    screenings: int
    next_starts_at: datetime
    seats_left: int
    rating: RatingSummary


class RatingDraft(Model):
    screening_id: UUID
    author_id: UUID
    author_name: str
    target_id: UUID
    target_role: Role
    score: int
    comment: str | None


class Rating(Model):
    id: UUID
    screening_id: UUID
    author_id: UUID
    author_name: str
    target_id: UUID
    target_role: Role
    score: int
    comment: str | None
    created_at: datetime


class Page[T](BaseModel):
    """Страница выдачи и сколько всего записей — для постраничной навигации."""

    model_config = ConfigDict(frozen=True)

    items: list[T]
    total: int
    page_number: int
    page_size: int


class PageRequest(Model):
    page_number: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1)

    @property
    def offset(self) -> int:
        return (self.page_number - 1) * self.page_size


class OutboxDraft(Model):
    """Событие для сервиса уведомлений, которое уйдёт после фиксации транзакции.

    `payload` — тело запроса `POST /notify/api/v1/events` без `event_id`:
    идентификатор события — это идентификатор строки outbox, его подставляет
    ретранслятор. Так повтор отправки не создаёт второе письмо (НФТ-6).
    """

    payload: dict[str, Any]
    request_id: str


class OutboxMessage(Model):
    id: UUID
    payload: dict[str, Any]
    request_id: str
    attempts: int
