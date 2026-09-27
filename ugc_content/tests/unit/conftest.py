"""Приложение на хранилищах в памяти и фабрика токенов."""

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from api.dependencies import Services, build_services
from core.config import Settings
from main import create_app
from services.content import BookmarkService, LikeService, ReviewService
from tests.unit import SECRET_KEY
from tests.unit.fakes import (
    InMemoryBookmarkStorage,
    InMemoryLikeStorage,
    InMemoryReviewStorage,
    ReadyHealthCheck,
)

API = '/content/api/v1'
REQUEST_ID_HEADER = 'X-Request-Id'
REQUEST_ID = 'unit-tests'
SESSION_ID = UUID('d3f1b4c2-51f9-4a1f-9f0e-2b6c1a7e4d11')
FILM_ID = UUID('b1a1d6f2-4f2a-4f0b-8f27-6b0a0e5f3f10')
OTHER_FILM_ID = UUID('c2b2e7a3-5a3b-4b1c-9a38-7c1b1f6a4a21')
# Маленькие пределы страницы, чтобы тесты упирались в них парой записей.
PAGE_SIZE_DEFAULT = 2
PAGE_SIZE_MAX = 3
# Маленький предел глубины, чтобы упереться в него на третьей странице.
MAX_PAGE_OFFSET = 4


@pytest.fixture
def settings() -> Settings:
    # Ключ подписи берётся из окружения (tests/unit/__init__.py): у него
    # объявлено имя переменной AUTH_JWT_SECRET_KEY, общее с сервисом авторизации.
    return Settings(
        mongo_uri='mongodb://mongo-does-not-exist:27017',
        page_size_default=PAGE_SIZE_DEFAULT,
        page_size_max=PAGE_SIZE_MAX,
        max_page_offset=MAX_PAGE_OFFSET,
    )


@pytest.fixture
def likes_storage() -> InMemoryLikeStorage:
    return InMemoryLikeStorage()


@pytest.fixture
def reviews_storage() -> InMemoryReviewStorage:
    return InMemoryReviewStorage()


@pytest.fixture
def bookmarks_storage() -> InMemoryBookmarkStorage:
    return InMemoryBookmarkStorage()


@pytest.fixture
def health() -> ReadyHealthCheck:
    return ReadyHealthCheck()


@pytest.fixture
def services(
    likes_storage: InMemoryLikeStorage,
    reviews_storage: InMemoryReviewStorage,
    bookmarks_storage: InMemoryBookmarkStorage,
    health: ReadyHealthCheck,
) -> Services:
    return build_services(
        likes=LikeService(likes_storage),
        reviews=ReviewService(reviews_storage),
        bookmarks=BookmarkService(bookmarks_storage),
        health=health,
    )


@pytest.fixture
def app(settings: Settings, services: Services):
    return create_app(settings, services=services)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    # Заголовок с идентификатором запроса ставит nginx, а без него сервис
    # отвечает 400 — поэтому клиент подставляет его во все запросы.
    with TestClient(app, headers={REQUEST_ID_HEADER: REQUEST_ID}) as test_client:
        yield test_client


@pytest.fixture
def user_id() -> UUID:
    return uuid4()


@pytest.fixture
def make_token() -> Callable[..., str]:
    """Выпускает токен так же, как сервис авторизации."""

    def factory(
        user: UUID | None = None,
        *,
        token_type: str = 'access',
        ttl: timedelta = timedelta(minutes=15),
        secret: str = SECRET_KEY,
        session_id: UUID = SESSION_ID,
        drop: str | None = None,
        sub: str | None = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            'sub': sub if sub is not None else str(user or uuid4()),
            'sid': str(session_id),
            'jti': uuid4().hex,
            'type': token_type,
            'iat': int(now.timestamp()),
            'exp': int((now + ttl).timestamp()),
        }
        if drop:
            payload.pop(drop)
        return jwt.encode(payload, secret, algorithm='HS256')

    return factory


@pytest.fixture
def auth(make_token: Callable[..., str], user_id: UUID) -> dict[str, str]:
    """Заголовок с действующим токеном зрителя."""
    return {'Authorization': f'Bearer {make_token(user_id)}'}
