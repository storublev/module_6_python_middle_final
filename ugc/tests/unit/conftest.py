"""Приложение с очередью в памяти и фабрика токенов."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest

from core.config import Settings
from main import create_app
from tests.unit import SECRET_KEY
from tests.unit.fakes import InMemoryEventQueue

API = '/ugc/api/v1'
REQUEST_ID_HEADER = 'X-Request-Id'
REQUEST_ID = 'unit-tests'
SESSION_ID = UUID('d3f1b4c2-51f9-4a1f-9f0e-2b6c1a7e4d11')
FILM_ID = UUID('b1a1d6f2-4f2a-4f0b-8f27-6b0a0e5f3f10')
# Небольшой предел пачки, чтобы тест упирался в него несколькими событиями.
MAX_EVENTS = 5


@pytest.fixture
def settings() -> Settings:
    # Ключ подписи берётся из окружения (tests/unit/__init__.py): у него
    # объявлено имя переменной AUTH_JWT_SECRET_KEY, общее с сервисом авторизации.
    return Settings(
        kafka_bootstrap_servers='kafka-does-not-exist:9092',
        max_events_per_request=MAX_EVENTS,
    )


@pytest.fixture
def queue() -> InMemoryEventQueue:
    return InMemoryEventQueue()


@pytest.fixture
def app(settings: Settings, queue: InMemoryEventQueue):
    return create_app(settings, queue=queue)


@pytest.fixture
def client(app):
    return app.test_client()


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
        now = datetime.now(UTC)
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
def headers(make_token: Callable[..., str], user_id: UUID) -> dict[str, str]:
    """Заголовки обычного запроса: токен и идентификатор запроса от шлюза."""
    return {
        'Authorization': f'Bearer {make_token(user_id)}',
        REQUEST_ID_HEADER: REQUEST_ID,
    }


@pytest.fixture
def click_event() -> dict:
    return {
        'event_type': 'click',
        'session_id': str(SESSION_ID),
        'occurred_at': '2026-09-21T19:04:11+03:00',
        'client': {'platform': 'web', 'device': 'Chrome 140', 'app_version': '2.14.0'},
        'element_type': 'film_card',
        'element_id': 'recommended-3',
        'page': '/catalog/drama',
        'film_id': str(FILM_ID),
    }


@pytest.fixture
def page_view_event() -> dict:
    return {
        'event_type': 'page_view',
        'session_id': str(SESSION_ID),
        'occurred_at': '2026-09-21T19:05:02+03:00',
        'client': {'platform': 'ios'},
        'page': f'/film/{FILM_ID}',
        'referrer': '/catalog/drama',
        'duration_ms': 51000,
    }
