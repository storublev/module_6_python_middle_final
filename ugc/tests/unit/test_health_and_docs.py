"""Проверки живости, готовности и документация OpenAPI."""

from http import HTTPStatus
from pathlib import Path

from export_openapi import render
from tests.unit.conftest import API

DOCS = '/ugc/api'
SPEC_FILE = Path(__file__).parents[2] / 'docs' / 'openapi.json'


def test_health_answers_without_request_id(client) -> None:
    """Живость проверяет healthcheck контейнера мимо шлюза, поэтому X-Request-Id не требуется."""
    response = client.get(f'{API}/health')

    assert response.status_code == HTTPStatus.OK
    assert response.json == {'status': 'ok'}


def test_ready_answers_ok_when_queue_is_connected(client) -> None:
    """Готовность подтверждается, когда есть соединение с брокером."""
    response = client.get(f'{API}/ready')

    assert response.status_code == HTTPStatus.OK


def test_ready_answers_503_without_queue(client, queue) -> None:
    """Без соединения с брокером сервис не готов принимать события."""
    queue.ready = False

    response = client.get(f'{API}/ready')

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json['status'] == 'unavailable'


def test_openapi_json_is_served(client) -> None:
    """Спецификация OpenAPI отдаётся сервисом и описывает эндпоинт приёма событий."""
    response = client.get(f'{DOCS}/openapi.json')

    assert response.status_code == HTTPStatus.OK
    assert '/events' in response.json['paths']


def test_every_response_of_the_events_endpoint_is_documented(client) -> None:
    """У приёма событий описаны все ответы, включая ошибочные, — требование проекта."""
    spec = client.get(f'{DOCS}/openapi.json').json

    documented = set(spec['paths']['/events']['post']['responses'])
    assert documented == {'202', '400', '401', '413', '422', '503', '500'}


def test_request_body_has_an_example(client) -> None:
    """У тела запроса есть пример: по документации видно, что именно присылать."""
    spec = client.get(f'{DOCS}/openapi.json').json

    example = spec['paths']['/events']['post']['requestBody']['content']['application/json']['example']
    assert {event['event_type'] for event in example['events']} >= {'click', 'page_view'}


def test_docs_page_is_served(client) -> None:
    """Страница документации открывается без заголовка от шлюза."""
    response = client.get(f'{DOCS}/openapi')

    assert response.status_code == HTTPStatus.OK
    assert 'swagger' in response.get_data(as_text=True).lower()


def test_openapi_file_is_up_to_date() -> None:
    """Файл спецификации в репозитории соответствует API; обновить: cd ugc/src && python export_openapi.py."""
    assert SPEC_FILE.read_text(encoding='utf-8') == render()
