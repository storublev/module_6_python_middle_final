"""HTTP-слой: статусы, формат ошибок и вход — на сервисах поверх хранилищ в памяти."""

import time
from datetime import timedelta
from http import HTTPStatus
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_booking_service, get_rating_service, get_screening_service
from api.security import get_verifier
from main import app
from tests.unit import SECRET_KEY
from tests.unit.conftest import GUEST, HOST, MOVIE, SERIES, World

PREFIX = '/booking/api/v1'
HEADERS = {'X-Request-Id': 'unit-test'}


def token(user_id, token_type: str = 'access', expires_in: int = 300) -> str:
    now = int(time.time())
    claims = {
        'sub': str(user_id), 'sid': str(uuid4()), 'jti': str(uuid4()), 'type': token_type,
        'iat': now, 'exp': now + expires_in,
    }
    return jwt.encode(claims, SECRET_KEY, algorithm='HS256')


def auth(user_id) -> dict[str, str]:
    return {**HEADERS, 'Authorization': f'Bearer {token(user_id)}'}


@pytest.fixture
def api(world: World):
    app.state.verifier = get_verifier()
    app.dependency_overrides = {
        get_screening_service: lambda: world.screenings,
        get_booking_service: lambda: world.bookings,
        get_rating_service: lambda: world.ratings,
    }
    yield TestClient(app)
    app.dependency_overrides = {}


def screening_body(world: World, **overrides) -> dict:
    body = {
        'film_id': str(MOVIE.id), 'starts_at': (world.clock() + timedelta(days=1)).isoformat(),
        'place': 'Зал 3', 'address': 'Новый Арбат, 24', 'capacity': 2,
    }
    return body | overrides


def test_full_booking_flow(api: TestClient, world: World):
    """Хост создаёт показ, гость находит хоста и дату в карточке фильма и бронирует."""
    created = api.post(f'{PREFIX}/screenings', json=screening_body(world), headers=auth(HOST))
    hosts = api.get(f'{PREFIX}/films/{MOVIE.id}/hosts', headers=HEADERS)
    dates = api.get(f'{PREFIX}/screenings', params={'film_id': str(MOVIE.id), 'host_id': str(HOST)}, headers=HEADERS)
    screening_id = created.json()['id']
    booked = api.post(f'{PREFIX}/screenings/{screening_id}/bookings', json={'seats': 2}, headers=auth(GUEST))
    shown = api.get(f'{PREFIX}/screenings/{screening_id}', headers=HEADERS)

    assert created.status_code == HTTPStatus.CREATED
    assert hosts.json()['items'][0]['host_name'] == 'Нео Андерсон'
    assert [item['id'] for item in dates.json()['items']] == [screening_id]
    assert booked.status_code == HTTPStatus.CREATED
    assert (shown.json()['seats_left'], shown.json()['seats_taken']) == (0, 2)


def test_not_enough_seats_is_409_with_code(api: TestClient, world: World):
    """Перебронирование — 409 с машиночитаемым кодом и числом оставшихся мест."""
    screening_id = api.post(f'{PREFIX}/screenings', json=screening_body(world), headers=auth(HOST)).json()['id']

    response = api.post(f'{PREFIX}/screenings/{screening_id}/bookings', json={'seats': 3}, headers=auth(GUEST))

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {'code': 'not_enough_seats', 'detail': 'Only 2 free seats left'}


@pytest.mark.parametrize(
    'body, status, code',
    [
        pytest.param({'film_id': str(SERIES.id)}, HTTPStatus.BAD_REQUEST, 'film_not_bookable', id='series'),
        pytest.param({'film_id': str(uuid4())}, HTTPStatus.NOT_FOUND, 'film_not_found', id='unknown-film'),
        pytest.param({'capacity': 51}, HTTPStatus.BAD_REQUEST, 'capacity_out_of_range', id='too-many-seats'),
    ],
)
def test_screening_errors(api: TestClient, world: World, body, status, code):
    """Ошибки создания показа — с кодами, а не 500."""
    response = api.post(f'{PREFIX}/screenings', json=screening_body(world, **body), headers=auth(HOST))

    assert (response.status_code, response.json()['code']) == (status, code)


def test_naive_time_is_rejected_without_echo(api: TestClient, world: World):
    """Время без пояса неоднозначно — 422, и присланные значения в ответ не возвращаются."""
    body = screening_body(world, starts_at='2026-10-17T19:00:00', address='Секретный адрес 7')

    response = api.post(f'{PREFIX}/screenings', json=body, headers=auth(HOST))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'Секретный адрес' not in response.text
    assert all('input' not in error for error in response.json()['detail'])


@pytest.mark.parametrize(
    'headers, code',
    [
        pytest.param(HEADERS, 'not_authenticated', id='no-token'),
        pytest.param({**HEADERS, 'Authorization': 'Bearer garbage'}, 'token_invalid', id='garbage'),
        pytest.param(
            {**HEADERS, 'Authorization': f'Bearer {token(HOST, expires_in=-1)}'}, 'token_expired', id='expired',
        ),
        pytest.param({**HEADERS, 'Authorization': f'Bearer {token(HOST, "refresh")}'}, 'token_invalid', id='refresh'),
    ],
)
def test_changes_require_access_token(api: TestClient, world: World, headers, code):
    """Изменения — только с действующим access-токеном; 401 сообщает схему в WWW-Authenticate."""
    response = api.post(f'{PREFIX}/screenings', json=screening_body(world), headers=headers)

    assert (response.status_code, response.json()['code']) == (HTTPStatus.UNAUTHORIZED, code)
    assert response.headers['WWW-Authenticate'].startswith('Bearer')


def test_reading_needs_no_token(api: TestClient):
    """Читать показы и рейтинги можно без входа."""
    assert api.get(f'{PREFIX}/screenings', headers=HEADERS).status_code == HTTPStatus.OK
    assert api.get(f'{PREFIX}/users/{HOST}/rating', headers=HEADERS).status_code == HTTPStatus.OK


def test_request_id_is_required_except_health(api: TestClient):
    """Запрос мимо шлюза (без X-Request-Id) отклоняется; проверка живости — нет."""
    assert api.get(f'{PREFIX}/screenings').json()['code'] == 'request_id_required'
    assert api.get(f'{PREFIX}/health').status_code == HTTPStatus.OK


def test_storage_outage_is_503(api: TestClient, world: World):
    """Каталог недоступен — 503 в общем формате, без подробностей."""
    world.catalog.available = False

    response = api.post(f'{PREFIX}/screenings', json=screening_body(world), headers=auth(HOST))

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()['code'] == 'service_unavailable'


def test_openapi_documents_error_codes(api: TestClient):
    """В документации у брони перечислены все её ошибки — клиенту не надо угадывать коды."""
    spec = api.get('/booking/api/openapi.json').json()
    responses = spec['paths'][f'{PREFIX}/screenings/{{screening_id}}/bookings']['post']['responses']

    assert {'401', '403', '404', '409', '400', '503'} <= set(responses)
    assert 'not_enough_seats' in responses['409']['content']['application/json']['examples']
