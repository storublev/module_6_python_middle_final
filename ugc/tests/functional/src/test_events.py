"""Приём событий: каждый ответ эндпоинта и то, что доезжает до брокера."""

from datetime import timedelta
from http import HTTPStatus

import pytest

from tests.functional.conftest import API, REQUEST_ID, REQUEST_ID_HEADER
from tests.functional.settings import settings

EVENTS = f'{API}/events'


def test_batch_is_accepted(http, url, headers, make_event) -> None:
    """Пачка корректных событий принимается с кодом 202."""
    events = [make_event('click'), make_event('page_view')]

    response = http.post(url(EVENTS), json={'events': events}, headers=headers)

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json() == {'accepted': 2, 'rejected': []}


def test_accepted_event_reaches_the_broker(http, url, headers, make_event, read_events, user_id) -> None:
    """Принятое событие оказывается в топике вместе с пользователем из токена."""
    event = make_event('click')

    http.post(url(EVENTS), json={'events': [event]}, headers=headers)

    delivered = read_events(1)
    assert len(delivered) == 1
    assert delivered[0]['event_id'] == event['event_id']
    assert delivered[0]['user_id'] == str(user_id)
    assert delivered[0]['received_at']


@pytest.mark.parametrize(
    'event_type',
    ['click', 'page_view', 'quality_changed', 'video_completed', 'search_filters_applied'],
)
def test_every_event_type_is_accepted(http, url, headers, make_event, read_events, event_type) -> None:
    """Сервис принимает все типы событий из контракта и передаёт их в брокер."""
    event = make_event(event_type)

    response = http.post(url(EVENTS), json={'events': [event]}, headers=headers)

    assert response.status_code == HTTPStatus.ACCEPTED
    assert read_events(1)[0]['event_type'] == event_type


def test_events_of_one_session_share_the_partition_key(http, url, headers, make_event, read_events) -> None:
    """События одной сессии просмотра едут с одним ключом — значит, в одну партицию."""
    events = [make_event('click'), make_event('page_view'), make_event('video_completed')]

    http.post(url(EVENTS), json={'events': events}, headers=headers)

    delivered = read_events(3)
    assert len({event['session_id'] for event in delivered}) == 1


def test_partially_broken_batch_is_accepted(http, url, headers, make_event, read_events) -> None:
    """Событие, не прошедшее контракт, отклоняется в одиночку: остальные доезжают."""
    good = make_event('click')

    response = http.post(url(EVENTS), json={'events': [good, {'event_type': 'like'}]}, headers=headers)

    assert response.status_code == HTTPStatus.ACCEPTED
    body = response.json()
    assert body['accepted'] == 1
    assert body['rejected'][0] == {
        'index': 1,
        'code': 'invalid_event',
        'detail': body['rejected'][0]['detail'],
    }
    assert read_events(1)[0]['event_id'] == good['event_id']


def test_batch_without_valid_events_is_rejected(http, url, headers, read_events) -> None:
    """Если не принято ни одного события — 422 и в брокер не уходит ничего."""
    response = http.post(url(EVENTS), json={'events': [{'event_type': 'like'}]}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()['accepted'] == 0
    assert read_events(1, timeout=3) == []


def test_user_id_from_body_is_not_trusted(http, url, headers, make_event) -> None:
    """Присланный клиентом user_id не принимается: пользователь берётся из токена."""
    event = make_event('click', user_id='00000000-0000-0000-0000-000000000000')

    response = http.post(url(EVENTS), json={'events': [event]}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()['rejected'][0]['code'] == 'invalid_event'


def test_empty_batch_is_rejected(http, url, headers) -> None:
    """Пустая пачка не принимается."""
    response = http.post(url(EVENTS), json={'events': []}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()['code'] == 'invalid_request'


def test_body_without_events_is_rejected(http, url, headers) -> None:
    """Тело без поля events не принимается."""
    response = http.post(url(EVENTS), json={}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()['code'] == 'invalid_request'


def test_non_json_body_is_rejected(http, url, headers) -> None:
    """Тело, которое не разбирается как JSON, не принимается."""
    response = http.post(
        url(EVENTS),
        data='не json',
        headers={**headers, 'Content-Type': 'application/json'},
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()['code'] == 'invalid_request'


def test_too_large_batch_is_rejected(http, url, headers, make_event) -> None:
    """Пачка длиннее предела не принимается целиком."""
    events = [make_event('click') for _ in range(settings.max_events_per_request + 1)]

    response = http.post(url(EVENTS), json={'events': events}, headers=headers)

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert response.json()['code'] == 'batch_too_large'


def test_request_without_token_is_rejected(http, url, make_event) -> None:
    """Без токена события не принимаются."""
    response = http.post(url(EVENTS), json={'events': [make_event()]})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'not_authenticated'


def test_request_with_expired_token_is_rejected(http, url, make_token, make_event) -> None:
    """С истёкшим токеном события не принимаются."""
    token = make_token(ttl=timedelta(minutes=-1))

    response = http.post(
        url(EVENTS),
        json={'events': [make_event()]},
        headers={'Authorization': f'Bearer {token}'},
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_expired'


def test_request_with_foreign_token_is_rejected(http, url, make_token, make_event) -> None:
    """Токен, подписанный чужим ключом, не принимается."""
    token = make_token(secret='another-secret-key-of-at-least-32-bytes')

    response = http.post(
        url(EVENTS),
        json={'events': [make_event()]},
        headers={'Authorization': f'Bearer {token}'},
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_invalid'


def test_refresh_token_is_not_accepted(http, url, make_token, make_event) -> None:
    """Refresh-токеном события слать нельзя."""
    token = make_token(token_type='refresh')

    response = http.post(
        url(EVENTS),
        json={'events': [make_event()]},
        headers={'Authorization': f'Bearer {token}'},
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_invalid'


def test_request_id_is_returned(http, url, headers, make_event) -> None:
    """Идентификатор запроса возвращается клиенту."""
    response = http.post(url(EVENTS), json={'events': [make_event()]}, headers=headers)

    assert response.headers[REQUEST_ID_HEADER] == REQUEST_ID


def test_get_is_not_allowed(http, url, headers) -> None:
    """Событие меняет состояние, поэтому принимается только POST."""
    response = http.get(url(EVENTS), headers=headers)

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert set(response.json()) == {'code', 'detail'}


def test_event_without_event_id_is_rejected(http, url, headers, make_event) -> None:
    """Событие без event_id не принимается: клиент обязан присылать его и сохранять при повторе."""
    event = make_event('click')
    del event['event_id']

    response = http.post(url(EVENTS), json={'events': [event]}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()['rejected'][0]['code'] == 'invalid_event'


def test_repeated_batch_keeps_the_same_event_ids(http, url, headers, make_event, read_events) -> None:
    """Повторная отправка той же пачки едет с теми же event_id — по ним аналитика уберёт дубль."""
    event = make_event('click')

    http.post(url(EVENTS), json={'events': [event]}, headers=headers)
    http.post(url(EVENTS), json={'events': [event]}, headers=headers)

    delivered = read_events(2)
    assert [item['event_id'] for item in delivered] == [event['event_id']] * 2


def test_element_of_wrong_type_does_not_reject_the_batch(http, url, headers, make_event, read_events) -> None:
    """Пачка с элементом null принимается частично: годное событие доезжает до брокера."""
    good = make_event('click')

    response = http.post(url(EVENTS), json={'events': [good, None]}, headers=headers)

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json()['accepted'] == 1
    assert response.json()['rejected'][0]['index'] == 1
    assert read_events(1)[0]['event_id'] == good['event_id']
