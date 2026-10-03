"""Интерфейс поверх подменённых API: каталога, бронирования и авторизации.

`Backends` — это три настоящих клиента интерфейса, но их транспорт отвечает
из словаря в памяти. Так проверяется весь путь страницы: какие запросы она
шлёт, с какими заголовками, и что показывает на ответы и отказы соседей.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from clients.api import AuthClient, BookingClient, CatalogClient
from main import Backends, create_app
from tests.unit import SECRET_KEY

USER_ID = '6f0c5c9c-4a55-4f6f-8f2a-2f1a5a8b0e11'
HOST_ID = 'c4b1a2d3-5e6f-4a7b-8c9d-0e1f2a3b4c5d'
FILM_ID = '3d825f60-9fff-4dfe-b294-1a45fa1e115d'
SERIES_ID = '0d6e2b3a-9a1c-4c7e-8b5f-3e2d1c0b9a87'
SCREENING_ID = '0b1e1d9a-6a4b-4f5e-9f2a-91f0c8c1c7a3'
HEADERS = {'X-Request-Id': 'req-test'}

FILM = {
    'uuid': FILM_ID, 'title': 'Star Wars', 'imdb_rating': 8.6, 'type': 'movie', 'imdb_id': 'tt0076759',
    'poster_url': 'https://m.media-amazon.com/images/M/sw._V1_QL75_UX400_.jpg', 'description': 'A long time ago',
    'genre': [{'uuid': str(uuid4()), 'name': 'Sci-Fi'}],
    'actors': [{'uuid': str(uuid4()), 'full_name': 'Mark Hamill'}],
    'writers': [], 'directors': [{'uuid': str(uuid4()), 'full_name': 'George Lucas'}],
}
SERIES = {**FILM, 'uuid': SERIES_ID, 'title': 'Star Trek', 'type': 'tv_show', 'poster_url': None}
SCREENING = {
    'id': SCREENING_ID, 'host_id': HOST_ID, 'host_name': 'Нео', 'film_id': FILM_ID, 'film_title': 'Star Wars',
    'film_poster': FILM['poster_url'], 'starts_at': '2099-10-17T16:00:00Z', 'place': 'Зал 3',
    'address': 'Новый Арбат, 24', 'description': None, 'capacity': 6, 'seats_taken': 2, 'seats_left': 4,
    'status': 'scheduled', 'created_at': '2026-10-01T10:00:00Z', 'updated_at': '2026-10-01T10:00:00Z',
}
HOST_OFFER = {
    'host_id': HOST_ID, 'host_name': 'Нео', 'screenings': 1, 'next_starts_at': SCREENING['starts_at'],
    'seats_left': 4, 'rating': {'average': 4.67, 'votes': 3},
}


def page(items: list) -> dict:
    return {'items': items, 'total': len(items), 'page_number': 1, 'page_size': 50}


def token(user_id: str = USER_ID, expires_in: int = 300) -> str:
    now = int(time.time())
    return jwt.encode(
        {'sub': user_id, 'sid': str(uuid4()), 'jti': str(uuid4()), 'type': 'access', 'iat': now,
         'exp': now + expires_in},
        SECRET_KEY, algorithm='HS256',
    )


Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeApi:
    """Ответы API по «метод путь»; всё, что не описано, — 404. Запросы запоминаются."""

    routes: dict[str, Any] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    down: bool = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError('down', request=request)
        reply = self.routes.get(f'{request.method} {request.url.path}')
        if reply is None:
            return httpx.Response(404, json={'detail': 'not found'})
        if callable(reply):
            return reply(request)
        status, body = reply if isinstance(reply, tuple) else (200, reply)
        return httpx.Response(status, json=body)

    def last(self, method: str, path: str) -> httpx.Request:
        return next(r for r in reversed(self.requests) if r.method == method and r.url.path == path)

    def body(self, method: str, path: str) -> Any:
        return json.loads(self.last(method, path).content)


@dataclass
class Stand:
    client: TestClient
    catalog: FakeApi
    booking: FakeApi
    auth: FakeApi

    def login(self, user_id: str = USER_ID, name: str = 'Тринити') -> None:
        self.client.cookies.set('practix_access', token(user_id))
        self.client.cookies.set('practix_refresh', 'refresh-token')
        self.client.cookies.set('practix_name', quote(name))


@pytest.fixture
def stand() -> Stand:
    catalog = FakeApi({
        f'GET /api/v1/films/{FILM_ID}': FILM,
        f'GET /api/v1/films/{SERIES_ID}': SERIES,
        'GET /api/v1/films': [FILM, SERIES],
        'GET /api/v1/genres': FILM['genre'],
    })
    booking = FakeApi({
        f'GET /booking/api/v1/films/{FILM_ID}/hosts': page([HOST_OFFER]),
        'GET /booking/api/v1/screenings': page([SCREENING]),
        f'GET /booking/api/v1/screenings/{SCREENING_ID}': SCREENING,
    })
    auth = FakeApi()

    def client(api: FakeApi) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url='http://backend', transport=httpx.MockTransport(api))

    app = create_app(Backends(CatalogClient(client(catalog)), BookingClient(client(booking)), AuthClient(client(auth))))
    test_client = TestClient(app, headers=HEADERS, follow_redirects=False)
    return Stand(test_client, catalog, booking, auth)
