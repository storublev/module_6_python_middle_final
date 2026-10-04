"""Общие фикстуры функциональных тестов.

Тесты ходят к сервису по HTTP и кода сервиса не импортируют. Зрителей заводит
настоящий сервис авторизации — с именами, которые сервис бронирования потом
берёт из его справочника.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import requests

from tests.functional.settings import settings

API = '/booking/api/v1'
AUTH_API = '/auth/api/v1'
PASSWORD = 'functional-Tests-1'
MOVIE_ID = '3d825f60-9fff-4dfe-b294-1a45fa1e115d'
SERIES_ID = '0d6e2b3a-9a1c-4c7e-8b5f-3e2d1c0b9a87'


class Viewer:
    """Зритель сервиса авторизации и его access-токен."""

    def __init__(self, user_id: str, name: str, token: str) -> None:
        self.user_id = user_id
        self.name = name
        self.token = token

    @property
    def headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.token}'}


def register(first_name: str = 'Томас', last_name: str = 'Андерсон') -> Viewer:
    login = f'viewer-{uuid.uuid4().hex[:12]}'
    auth = f'{settings.auth_url}{AUTH_API}'
    user_id = requests.post(f'{auth}/signup', json={'login': login, 'password': PASSWORD}, timeout=10).json()['id']
    token = requests.post(
        f'{auth}/login', json={'login': login, 'password': PASSWORD}, timeout=10,
    ).json()['access_token']
    requests.patch(
        f'{auth}/users/me/profile', json={'first_name': first_name, 'last_name': last_name},
        headers={'Authorization': f'Bearer {token}'}, timeout=10,
    ).raise_for_status()
    return Viewer(user_id, f'{first_name} {last_name}', token)


def call(
    method: str, path: str, viewer: Viewer | None = None, headers: dict[str, str] | None = None, **kwargs: Any,
) -> requests.Response:
    merged = {**(viewer.headers if viewer else {}), **(headers or {})}
    return requests.request(method, f'{settings.service_url}{API}{path}', headers=merged, timeout=15, **kwargs)


def starts_in(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def new_screening(host: Viewer, capacity: int = 6, delta: timedelta = timedelta(days=2), **fields: Any) -> dict:
    body = {
        'film_id': MOVIE_ID, 'starts_at': starts_in(delta), 'place': 'Кинотеатр «Октябрь», зал 3',
        'address': 'Москва, Новый Арбат, 24', 'capacity': capacity, **fields,
    }
    response = call('POST', '/screenings', host, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def soon_started(host: Viewer, *guests: Viewer, capacity: int = 6) -> dict:
    """Показ, который начинается через пару секунд: гости бронируют, затем он начинается."""
    screening = new_screening(host, capacity, timedelta(seconds=settings.min_lead_seconds + 2))
    for guest in guests:
        assert call('POST', f'/screenings/{screening["id"]}/bookings', guest, json={'seats': 1}).status_code == 201
    wait_until = datetime.fromisoformat(screening['starts_at'])
    time.sleep(max(0.0, (wait_until - datetime.now(UTC)).total_seconds()) + 0.5)
    return screening


def stub(method: str, path: str) -> requests.Response:
    return requests.request(method, f'{settings.stub_url}{path}', timeout=5)


def events() -> list[dict]:
    return stub('GET', '/_events').json()


@pytest.fixture
def host() -> Viewer:
    return register('Нео', 'Андерсон')


@pytest.fixture
def guest() -> Viewer:
    return register('Тринити', 'Ноль')


@pytest.fixture
def other() -> Viewer:
    return register('Морфеус', 'Капитан')


@pytest.fixture(autouse=True)
def neighbours_up():
    """Заглушка соседей в исходном состоянии: событий нет, сервис уведомлений жив."""
    stub('POST', '/_reset')
    yield
    stub('POST', '/_up')
