"""Проверки живости и готовности, документация и идентификатор запроса."""

from http import HTTPStatus

import requests

from tests.functional.conftest import API, DOCS, REQUEST_ID_HEADER


def test_health_answers(http, url) -> None:
    """Живость подтверждается, пока жив процесс."""
    response = http.get(url(f'{API}/health'))

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {'status': 'ok'}


def test_ready_answers(http, url) -> None:
    """Готовность подтверждается: соединение с брокером есть."""
    response = http.get(url(f'{API}/ready'))

    assert response.status_code == HTTPStatus.OK


def test_openapi_is_served(http, url) -> None:
    """Сервис отдаёт спецификацию OpenAPI."""
    response = http.get(url(f'{DOCS}/openapi.json'))

    assert response.status_code == HTTPStatus.OK
    assert '/events' in response.json()['paths']


def test_docs_page_is_served(http, url) -> None:
    """Страница документации открывается."""
    response = http.get(url(f'{DOCS}/openapi'))

    assert response.status_code == HTTPStatus.OK
    assert 'swagger' in response.text.lower()


def test_request_without_request_id_is_rejected(url, headers, make_event) -> None:
    """Запрос мимо шлюза отклоняется: без X-Request-Id его не найти в журналах."""
    # Отдельный запрос, а не фикстура http: у неё заголовок проставлен всегда.
    response = requests.post(
        url(f'{API}/events'),
        json={'events': [make_event()]},
        headers=headers,
        timeout=10,
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()['code'] == 'request_id_required'


def test_health_does_not_require_request_id(url) -> None:
    """Проверку живости дёргает healthcheck контейнера — заголовка у него нет."""
    response = requests.get(url(f'{API}/health'), timeout=10)

    assert response.status_code == HTTPStatus.OK
    assert REQUEST_ID_HEADER not in response.headers


def test_unknown_path_answers_in_the_error_format(http, url) -> None:
    """Неизвестный адрес отвечает тем же форматом ошибки, а не HTML-страницей."""
    response = http.get(url(f'{API}/unknown'))

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert set(response.json()) == {'code', 'detail'}
