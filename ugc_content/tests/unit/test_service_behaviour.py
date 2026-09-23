"""Служебное поведение: недоступность хранилища, идентификатор запроса, готовность, документация."""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from api.dependencies import build_services
from api.errors import error_responses
from core.config import Settings
from main import create_app
from services.content import BookmarkService, LikeService, ReviewService
from services.errors import NotAuthenticatedError, ReviewNotFoundError
from tests.unit.conftest import API, FILM_ID, REQUEST_ID, REQUEST_ID_HEADER
from tests.unit.fakes import (
    BrokenLikeStorage,
    ReadyHealthCheck,
)

DOCS = '/content/api/openapi'


@pytest.fixture
def broken_client(settings: Settings, reviews_storage, bookmarks_storage, health):
    """Приложение, у которого хранилище оценок всегда недоступно."""
    services = build_services(
        likes=LikeService(BrokenLikeStorage()),
        reviews=ReviewService(reviews_storage),
        bookmarks=BookmarkService(bookmarks_storage),
        health=health,
    )
    with TestClient(create_app(settings, services=services),
                    headers={REQUEST_ID_HEADER: REQUEST_ID}) as client:
        yield client


def test_storage_failure_answers_503(broken_client):
    """Сбой хранилища — 503 с кодом service_unavailable, а не 500."""
    response = broken_client.get(f'{API}/films/{FILM_ID}/rating')

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()['code'] == 'service_unavailable'


def test_storage_failure_hides_internals(broken_client):
    """В ответе нет подробностей сбоя: клиенту они не нужны, а в журнале они есть."""
    response = broken_client.get(f'{API}/films/{FILM_ID}/rating')

    assert 'MongoDB' not in response.json()['detail']


def test_request_id_is_returned(client):
    """Идентификатор запроса возвращается клиенту: по нему запрос ищут в логах."""
    response = client.get(f'{API}/films/{FILM_ID}/rating')

    assert response.headers[REQUEST_ID_HEADER] == REQUEST_ID


def test_request_without_request_id_is_rejected(app):
    """Запрос мимо шлюза, без X-Request-Id, отклоняется с 400."""
    with TestClient(app) as bare_client:
        response = bare_client.get(f'{API}/films/{FILM_ID}/rating')

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()['code'] == 'request_id_required'


def test_health_does_not_require_request_id(app):
    """Проверку живости дёргает healthcheck контейнера — заголовка у него нет."""
    with TestClient(app) as bare_client:
        response = bare_client.get(f'{API}/health')

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {'status': 'ok'}


def test_ready_reports_storage_state(client, health: ReadyHealthCheck):
    """Готовность зависит от хранилища: молчит MongoDB — сервис не готов."""
    assert client.get(f'{API}/ready').status_code == HTTPStatus.OK

    health.ready = False
    response = client.get(f'{API}/ready')

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()['status'] == 'not ready'


def test_openapi_is_published(client):
    """Документация отдаётся по своему адресу и описывает все эндпоинты."""
    response = client.get(f'{DOCS}.json')

    assert response.status_code == HTTPStatus.OK
    paths = response.json()['paths']
    assert f'{API}/films/{{film_id}}/rating' in paths
    assert f'{API}/films/{{film_id}}/reviews' in paths
    assert f'{API}/users/me/bookmarks' in paths


def test_openapi_describes_error_responses(client):
    """У защищённых эндпоинтов в документации описаны ответы с ошибками."""
    schema = client.get(f'{DOCS}.json').json()
    responses = schema['paths'][f'{API}/films/{{film_id}}/rating']['put']['responses']

    assert '401' in responses
    assert '503' in responses


def test_error_responses_use_plain_int_keys():
    """Ключи ответов OpenAPI — числа, а не HTTPStatus.

    FastAPI переводит ключ в строку через `str()`, и у `IntEnum` это поведение
    менялось: до Python 3.11 из `HTTPStatus.UNAUTHORIZED` получалась строка
    `'HTTPStatus.UNAUTHORIZED'`, и описания ошибок пропадали из документации.
    Этот тест ловит возврат к `HTTPStatus` на любой версии Python, а не только
    на той, где ошибка видна.
    """
    responses = error_responses(NotAuthenticatedError, ReviewNotFoundError)

    assert all(type(key) is int for key in responses), responses.keys()
    assert {401, 404, 503} <= set(responses)


def test_unknown_path_returns_404(client):
    """Несуществующий адрес отвечает 404, а не падает."""
    response = client.get(f'{API}/films/unknown-path')

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_unused_in_memory_storages_are_independent(client, auth, reviews_storage, bookmarks_storage):
    """Оценка не задевает соседние хранилища: слои не перепутаны."""
    client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 5}, headers=auth)

    assert reviews_storage.items == {}
    assert bookmarks_storage.items == {}
