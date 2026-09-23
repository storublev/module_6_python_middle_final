"""Composition Root: здесь и только здесь выбираются конкретные реализации.

Эндпоинты просят интерфейс, а какой класс за ним стоит — решается в этом
модуле. Поэтому подменить MongoDB на хранилище в памяти (в unit-тестах) можно,
не трогая ни один эндпоинт: достаточно положить другие сервисы в
`app.state`.

Так же устроены Async API и сервис сбора событий — это общий приём проекта, а
не изобретение этого сервиса.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request

from core.config import Settings
from services.content import BookmarkService, LikeService, ReviewService
from storage.base import HealthCheck


@dataclass(frozen=True)
class Services:
    """Всё, что нужно эндпоинтам, одним объектом."""

    likes: LikeService
    reviews: ReviewService
    bookmarks: BookmarkService
    health: HealthCheck


def build_services(
    likes: LikeService,
    reviews: ReviewService,
    bookmarks: BookmarkService,
    health: HealthCheck,
) -> Services:
    """Собирает сервисы из готовых хранилищ."""
    return Services(likes=likes, reviews=reviews, bookmarks=bookmarks, health=health)


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_likes(services: Annotated[Services, Depends(get_services)]) -> LikeService:
    return services.likes


def get_reviews(services: Annotated[Services, Depends(get_services)]) -> ReviewService:
    return services.reviews


def get_bookmarks(services: Annotated[Services, Depends(get_services)]) -> BookmarkService:
    return services.bookmarks


def get_health(services: Annotated[Services, Depends(get_services)]) -> HealthCheck:
    return services.health


@dataclass(frozen=True)
class Pagination:
    """Страница списка."""

    page: int
    size: int


def pagination(
    request: Request,
    page: Annotated[int, Query(ge=1, description='Номер страницы, с единицы')] = 1,
    size: Annotated[int | None, Query(ge=1, description='Сколько записей на странице')] = None,
) -> Pagination:
    """Разбор параметров страницы.

    Предел размера страницы — настройка сервиса, а не константа в схеме:
    длинная страница ломает бюджет в 200 мс, и подобрать её предел должно
    быть можно без пересборки образа.
    """
    config: Settings = request.app.state.settings
    return Pagination(page=page, size=min(size or config.page_size_default, config.page_size_max))


Likes = Annotated[LikeService, Depends(get_likes)]
Reviews = Annotated[ReviewService, Depends(get_reviews)]
Bookmarks = Annotated[BookmarkService, Depends(get_bookmarks)]
Health = Annotated[HealthCheck, Depends(get_health)]
Page = Annotated[Pagination, Depends(pagination)]
