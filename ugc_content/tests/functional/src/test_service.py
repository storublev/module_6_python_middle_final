"""Служебное поведение живого сервиса: документация, готовность, идентификатор запроса, ошибки."""

from http import HTTPStatus
from uuid import uuid4

import requests

from tests.functional.conftest import API, DOCS, REQUEST_ID, REQUEST_ID_HEADER
from tests.functional.settings import settings


def test_health_is_open(http, url):
    """Проверка живости отвечает без токена и без заголовков."""
    response = requests.get(url(f'{API}/health'), timeout=5)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {'status': 'ok'}


def test_ready_reports_storage(http, url):
    """Готовность подтверждает, что MongoDB отвечает."""
    response = http.get(url(f'{API}/ready'))

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {'status': 'ready'}


def test_openapi_is_published(http, url):
    """Сервис отдаёт собственную документацию (ФТ-14)."""
    response = http.get(url(f'{DOCS}/openapi.json'))

    assert response.status_code == HTTPStatus.OK
    assert f'{API}/films/{{film_id}}/rating' in response.json()['paths']


def test_request_id_is_echoed(http, url, film_id):
    """Идентификатор запроса возвращается клиенту и попадает в журнал сервиса."""
    response = http.get(url(f'{API}/films/{film_id}/rating'))

    assert response.headers[REQUEST_ID_HEADER] == REQUEST_ID


def test_request_without_request_id_is_rejected(url, film_id):
    """Запрос мимо шлюза отклоняется: без идентификатора его не найти в логах."""
    response = requests.get(url(f'{API}/films/{film_id}/rating'), timeout=5)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()['code'] == 'request_id_required'


def test_expired_token_is_rejected(http, url, make_token, film_id):
    """Истёкший токен отвечает 401 с кодом token_expired."""
    from datetime import timedelta

    token = make_token(uuid4(), ttl=timedelta(minutes=-1))

    response = http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 5},
                        headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_expired'


def test_foreign_token_is_rejected(http, url, make_token, film_id):
    """Токен, подписанный чужим секретом, не принимается."""
    token = make_token(uuid4(), secret='not-the-service-secret-key-32-bytes')

    response = http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 5},
                        headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_invalid'


def test_invalid_rating_is_rejected(http, url, auth, film_id):
    """Оценка вне диапазона не принимается и не доходит до хранилища."""
    response = http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 100}, headers=auth)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_validation_error_does_not_echo_text(http, url, auth, film_id):
    """Ответ 422 не повторяет присланный текст рецензии."""
    secret_text = 'а' * 20_000

    response = http.post(url(f'{API}/films/{film_id}/reviews'), json={'text': secret_text}, headers=auth)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert secret_text not in response.text


def test_page_size_is_capped(http, url, auth):
    """Размер страницы обрезается настройкой сервиса, а не берётся из запроса."""
    response = http.get(url(f'{API}/users/me/bookmarks'), params={'size': 10_000}, headers=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json()['size'] == settings.page_size_max


def test_unknown_path_is_404(http, url):
    """Несуществующий адрес отвечает 404."""
    response = http.get(url(f'{API}/films'))

    assert response.status_code == HTTPStatus.NOT_FOUND
