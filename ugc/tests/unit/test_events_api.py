"""Эндпоинт приёма событий: все его ответы."""

from datetime import timedelta
from http import HTTPStatus

import pytest

from core.config import Settings
from main import create_app
from tests.unit.conftest import API, MAX_EVENTS, REQUEST_ID, REQUEST_ID_HEADER
from tests.unit.fakes import InMemoryEventQueue

EVENTS = f'{API}/events'


def test_batch_is_accepted(client, headers, queue, click_event, page_view_event) -> None:
    """Пачка корректных событий принимается с кодом 202 и уходит в очередь."""
    response = client.post(EVENTS, json={'events': [click_event, page_view_event]}, headers=headers)

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json == {'accepted': 2, 'rejected': []}
    assert len(queue.published) == 2


def test_partially_broken_batch_is_accepted(client, headers, queue, click_event) -> None:
    """Одно испорченное событие не отменяет остальные: 202 и перечень отклонённых."""
    response = client.post(EVENTS, json={'events': [click_event, {'event_type': 'like'}]}, headers=headers)

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json['accepted'] == 1
    assert response.json['rejected'][0]['index'] == 1
    assert response.json['rejected'][0]['code'] == 'invalid_event'
    assert len(queue.published) == 1


def test_batch_without_valid_events_is_rejected(client, headers, queue) -> None:
    """Если не принято ни одного события — 422: повторять такой запрос бессмысленно."""
    response = client.post(EVENTS, json={'events': [{'event_type': 'like'}]}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json['accepted'] == 0
    assert queue.published == []


def test_empty_batch_is_rejected(client, headers) -> None:
    """Пустая пачка не принимается: отправлять нечего."""
    response = client.post(EVENTS, json={'events': []}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json['code'] == 'invalid_request'


def test_body_without_events_is_rejected(client, headers) -> None:
    """Тело без поля events не принимается."""
    response = client.post(EVENTS, json={}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json['code'] == 'invalid_request'
    assert 'events' in response.json['detail']


def test_unknown_field_in_body_is_rejected(client, headers, click_event) -> None:
    """Лишнее поле в теле запроса не принимается."""
    response = client.post(EVENTS, json={'events': [click_event], 'source': 'curl'}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json['code'] == 'invalid_request'


def test_non_json_body_is_rejected(client, headers) -> None:
    """Тело, которое не разбирается как JSON, не принимается."""
    response = client.post(EVENTS, data='не json', content_type='application/json', headers=headers)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json['code'] == 'invalid_request'


def test_too_large_batch_is_rejected(client, headers, queue, click_event) -> None:
    """Пачка длиннее предела не принимается целиком: 413."""
    response = client.post(EVENTS, json={'events': [click_event] * (MAX_EVENTS + 1)}, headers=headers)

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert response.json['code'] == 'batch_too_large'
    assert queue.published == []


def test_request_without_token_is_rejected(client, click_event) -> None:
    """Без токена события не принимаются: каждое событие соотносится с пользователем."""
    response = client.post(
        EVENTS,
        json={'events': [click_event]},
        headers={REQUEST_ID_HEADER: REQUEST_ID},
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json['code'] == 'not_authenticated'


def test_request_with_expired_token_is_rejected(client, make_token, click_event) -> None:
    """С истёкшим токеном события не принимаются."""
    response = client.post(
        EVENTS,
        json={'events': [click_event]},
        headers={
            'Authorization': f'Bearer {make_token(ttl=timedelta(minutes=-1))}',
            REQUEST_ID_HEADER: REQUEST_ID,
        },
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json['code'] == 'token_expired'


def test_request_without_request_id_is_rejected(client, headers, click_event) -> None:
    """Запрос мимо шлюза отклоняется: без X-Request-Id его не найти ни в журналах, ни в Jaeger."""
    headers.pop(REQUEST_ID_HEADER)

    response = client.post(EVENTS, json={'events': [click_event]}, headers=headers)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json['code'] == 'request_id_required'


def test_request_id_is_returned_to_the_client(client, headers, click_event) -> None:
    """Идентификатор запроса возвращается в ответе: по нему запрос ищут в поддержке."""
    response = client.post(EVENTS, json={'events': [click_event]}, headers=headers)

    assert response.headers[REQUEST_ID_HEADER] == REQUEST_ID


def test_queue_failure_answers_503(headers, click_event) -> None:
    """Недоступность брокера — 503: клиент не удалит события и повторит запрос."""
    app = create_app(Settings(max_events_per_request=MAX_EVENTS), queue=InMemoryEventQueue(available=False))

    response = app.test_client().post(EVENTS, json={'events': [click_event]}, headers=headers)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json['code'] == 'queue_unavailable'


def test_unknown_path_answers_in_the_error_format(client, headers) -> None:
    """Неизвестный адрес отвечает тем же форматом ошибки, а не HTML-страницей."""
    response = client.get(f'{API}/unknown', headers=headers)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert set(response.json) == {'code', 'detail'}


def test_get_is_not_allowed(client, headers) -> None:
    """Событие меняет состояние, поэтому принимается только POST."""
    response = client.get(EVENTS, headers=headers)

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert set(response.json) == {'code', 'detail'}


@pytest.mark.parametrize('required', [True, False])
def test_request_id_check_is_configurable(required, click_event, headers) -> None:
    """Проверку X-Request-Id можно выключить: локально сервис запускают без nginx."""
    app = create_app(
        Settings(max_events_per_request=MAX_EVENTS, require_request_id=required),
        queue=InMemoryEventQueue(),
    )
    headers.pop(REQUEST_ID_HEADER)

    response = app.test_client().post(EVENTS, json={'events': [click_event]}, headers=headers)

    assert response.status_code == (HTTPStatus.BAD_REQUEST if required else HTTPStatus.ACCEPTED)
