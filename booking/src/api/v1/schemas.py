"""Схемы запросов и ответов API. Описания и примеры попадают в OpenAPI.

Ответы строятся из моделей сервиса (`from_attributes`), поэтому эндпоинты
возвращают модели как есть, а лишние поля (например, внутренние счётчики)
наружу не попадают.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from core.config import settings
from models.domain import BookingStatus, PageRequest, Role, ScreeningStatus

HOST_ID = '6f0c5c9c-4a55-4f6f-8f2a-2f1a5a8b0e11'
FILM_ID = '3d825f60-9fff-4dfe-b294-1a45fa1e115d'
SCREENING_ID = '0b1e1d9a-6a4b-4f5e-9f2a-91f0c8c1c7a3'
STARTS_AT = '2026-10-17T16:00:00Z'

Place = Annotated[
    str, Field(min_length=1, max_length=255, description='Где собираемся', examples=['Кинотеатр «Октябрь», зал 3']),
]
Address = Annotated[
    str, Field(min_length=1, max_length=512, description='Адрес', examples=['Москва, Новый Арбат, 24'])
]
Description = Annotated[
    str | None,
    Field(
        default=None, max_length=2000, description='Что ещё надо знать гостям',
        examples=['После фильма — обсуждение в кафе'],
    ),
]
StartsAt = Annotated[
    AwareDatetime,
    Field(description='Начало показа с часовым поясом (ISO 8601). Хранится в UTC', examples=[STARTS_AT]),
]


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RatingSummarySchema(Schema):
    """Рейтинг в одной роли."""

    average: float | None = Field(description='Средняя оценка от 1 до 5; null — оценок ещё нет', examples=[4.67])
    votes: int = Field(description='Сколько оценок', examples=[3])


class ScreeningSchema(Schema):
    """Показ: хост предлагает фильм, место и время."""

    id: UUID = Field(examples=[SCREENING_ID])
    host_id: UUID = Field(description='Хост — кто предлагает фильм и место', examples=[HOST_ID])
    host_name: str = Field(description='Имя хоста на момент создания показа', examples=['Нео Андерсон'])
    film_id: UUID = Field(examples=[FILM_ID])
    film_title: str = Field(examples=['Star Wars: Episode IV - A New Hope'])
    film_poster: str | None = Field(description='Обложка фильма; null — нет обложки', examples=[None])
    starts_at: datetime = Field(description='Начало показа, UTC', examples=[STARTS_AT])
    place: str = Field(examples=['Кинотеатр «Октябрь», зал 3'])
    address: str = Field(examples=['Москва, Новый Арбат, 24'])
    description: str | None = Field(examples=['После фильма — обсуждение в кафе'])
    capacity: int = Field(description='Сколько всего мест', examples=[6])
    seats_taken: int = Field(description='Сколько забронировано', examples=[4])
    seats_left: int = Field(description='Сколько свободно', examples=[2])
    status: ScreeningStatus = Field(examples=['scheduled'])
    created_at: datetime
    updated_at: datetime


class ScreeningCreateSchema(BaseModel):
    """Новый показ. Хост — тот, кто прислал запрос."""

    film_id: UUID = Field(description='Фильм из каталога; только полнометражный (type = movie)', examples=[FILM_ID])
    starts_at: StartsAt
    place: Place
    address: Address
    description: Description
    capacity: int = Field(
        description=f'Сколько мест у хоста: от {settings.min_capacity} до {settings.max_capacity}', examples=[6],
    )


class ScreeningUpdateSchema(BaseModel):
    """Что изменить в показе. Хотя бы одно поле; не переданное — не меняется."""

    starts_at: StartsAt | None = None
    place: Place | None = None
    address: Address | None = None
    description: Description = None
    capacity: int | None = Field(default=None, description='Не меньше уже забронированного', examples=[8])


class BookingSchema(Schema):
    """Бронь гостя."""

    id: UUID = Field(examples=['9a7c2f1e-1d5b-4a8e-b3a1-2f4b7c9d0e11'])
    screening_id: UUID = Field(examples=[SCREENING_ID])
    guest_id: UUID = Field(examples=['c4b1a2d3-5e6f-4a7b-8c9d-0e1f2a3b4c5d'])
    guest_name: str = Field(examples=['Тринити'])
    seats: int = Field(description='Сколько мест занято', examples=[2])
    status: BookingStatus = Field(examples=['active'])
    created_at: datetime
    updated_at: datetime


class BookingCreateSchema(BaseModel):
    seats: int = Field(
        default=1, description=f'Сколько мест: от 1 до {settings.max_seats_per_booking}', examples=[2],
    )


class BookingUpdateSchema(BaseModel):
    seats: int = Field(description=f'Новое число мест: от 1 до {settings.max_seats_per_booking}', examples=[3])


class BookingViewSchema(Schema):
    """Бронь вместе с показом — строка страницы «Мои брони»."""

    booking: BookingSchema
    screening: ScreeningSchema


class GuestSchema(Schema):
    """Гость показа и его рейтинг как гостя — видит только хост."""

    booking: BookingSchema
    rating: RatingSummarySchema


class HostOfferSchema(Schema):
    """Хост, который показывает фильм."""

    host_id: UUID = Field(examples=[HOST_ID])
    host_name: str = Field(examples=['Нео Андерсон'])
    screenings: int = Field(description='Сколько будущих показов этого фильма', examples=[2])
    next_starts_at: datetime = Field(description='Ближайший показ', examples=[STARTS_AT])
    seats_left: int = Field(description='Свободных мест на всех его показах фильма', examples=[5])
    rating: RatingSummarySchema = Field(description='Рейтинг хоста')


class RatingSchema(Schema):
    """Оценка участника показа."""

    id: UUID
    screening_id: UUID = Field(examples=[SCREENING_ID])
    author_id: UUID
    author_name: str = Field(examples=['Тринити'])
    target_id: UUID = Field(examples=[HOST_ID])
    target_role: Role = Field(description='Кем был оцениваемый на показе', examples=['host'])
    score: int = Field(examples=[5])
    comment: str | None = Field(examples=['Отличная компания и удобный зал'])
    created_at: datetime


class RatingCreateSchema(BaseModel):
    """Оценка: гость оценивает хоста, хост — гостя. После начала показа."""

    target_id: UUID = Field(description='Кого оцениваем', examples=[HOST_ID])
    score: int = Field(ge=1, le=5, description='От 1 до 5', examples=[5])
    comment: str | None = Field(default=None, max_length=1000, examples=['Отличная компания и удобный зал'])


class UserRatingSchema(Schema):
    """Рейтинг зрителя как хоста и как гостя."""

    user_id: UUID = Field(examples=[HOST_ID])
    name: str | None = Field(description='Имя из последнего показа или брони; null — не участвовал', examples=['Нео'])
    as_host: RatingSummarySchema
    as_guest: RatingSummarySchema


class PageSchema[T](BaseModel):
    """Страница выдачи."""

    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int = Field(description='Сколько всего записей', examples=[42])
    page_number: int = Field(examples=[1])
    page_size: int = Field(examples=[20])


def pagination(
    page_number: Annotated[int, Query(ge=1, le=10_000, description='Номер страницы, начиная с 1')] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=settings.page_size_max, description='Сколько записей на странице'),
    ] = settings.page_size_default,
) -> PageRequest:
    return PageRequest(page_number=page_number, page_size=page_size)


PaginationDep = Annotated[PageRequest, Depends(pagination)]
