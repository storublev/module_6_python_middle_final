"""Адаптеры соседних сервисов: что считается ответом, а что — сбоем."""

import json
from uuid import uuid4

import httpx
import pytest

from core.request_id import set_request_id
from storage.base import EventRejectedError, StorageUnavailableError
from storage.http import HttpCatalog, HttpNotifications, HttpPeople, HttpSessions, display_name
from storage.resilience import CircuitBreaker

FILM_ID = uuid4()


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url='http://neighbour', transport=httpx.MockTransport(handler))


async def test_catalog_returns_film_and_forwards_token_and_request_id():
    """Каталог получает токен зрителя и X-Request-Id; из ответа берутся тип и обложка."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={
            'uuid': str(FILM_ID), 'title': 'Star Wars', 'type': 'movie', 'poster_url': 'https://e.com/p.jpg',
        })

    set_request_id('req-42')
    film = await HttpCatalog(client(handler)).film(FILM_ID, 'Bearer abc')

    assert (film.title, film.type, film.poster_url) == ('Star Wars', 'movie', 'https://e.com/p.jpg')
    assert (seen['authorization'], seen['x-request-id']) == ('Bearer abc', 'req-42')


@pytest.mark.parametrize('status', [404, 403])
async def test_catalog_missing_or_closed_film_is_none(status):
    """Нет фильма или он только по подписке — для показа его нет; это ответ, а не сбой."""
    catalog = HttpCatalog(client(lambda _: httpx.Response(status, json={'detail': 'x'})))

    assert await catalog.film(FILM_ID, None) is None


async def test_server_error_opens_breaker():
    """5xx — сбой; после серии сбоев прерыватель не пускает запросы вовсе."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    catalog = HttpCatalog(client(handler), CircuitBreaker(failures=2, reset_timeout=60))
    for _ in range(3):
        with pytest.raises(StorageUnavailableError):
            await catalog.film(FILM_ID, None)

    assert len(calls) == 2


async def test_connection_error_is_storage_error():
    """Нет соединения — StorageUnavailableError, а не исключение httpx."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('refused', request=request)

    with pytest.raises(StorageUnavailableError):
        await HttpCatalog(client(handler)).film(FILM_ID, None)


async def test_people_sends_service_token_and_builds_names():
    """Справочник спрашивается служебным секретом пачкой; имя — «Имя Фамилия», иначе логин."""
    first, second = uuid4(), uuid4()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['token'] = request.headers['x-service-token']
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json=[
            {'id': str(first), 'login': 'neo', 'first_name': 'Томас', 'last_name': 'Андерсон'},
            {'id': str(second), 'login': 'trinity', 'first_name': None, 'last_name': None},
        ])

    names = await HttpPeople(client(handler), 'secret').names([first, second])

    assert names == {first: 'Томас Андерсон', second: 'trinity'}
    assert (seen['token'], len(seen['body']['user_ids'])) == ('secret', 2)


@pytest.mark.parametrize('status, active', [(200, True), (401, False)])
async def test_sessions_ask_auth_with_viewer_token(status, active):
    """Живость сессии — ответ сервиса авторизации на токен зрителя: 401 значит «сессия закрыта»."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(path=request.url.path, authorization=request.headers['authorization'])
        return httpx.Response(status, json={})

    assert await HttpSessions(client(handler)).is_active('Bearer abc') is active
    assert seen == {'path': '/auth/api/v1/users/me', 'authorization': 'Bearer abc'}


@pytest.mark.parametrize('status', [403, 500, 503])
async def test_sessions_other_answers_are_outage(status):
    """Любой другой ответ — не «сессия закрыта», а сбой: запись получит 503, а не ложный 401."""
    with pytest.raises(StorageUnavailableError):
        await HttpSessions(client(lambda _: httpx.Response(status, json={}))).is_active('Bearer abc')


def test_display_name_with_only_last_name():
    """Одна фамилия — тоже имя, без лишнего пробела."""
    assert display_name({'login': 'neo', 'first_name': '', 'last_name': 'Андерсон'}) == 'Андерсон'


async def test_notifications_get_event_id_and_original_request_id():
    """В сервис уведомлений уходит event_id из outbox и request_id той брони, что породила событие."""
    event_id = uuid4()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['body'] = json.loads(request.content)
        seen['request_id'] = request.headers['x-request-id']
        return httpx.Response(202, json={'event_id': str(event_id), 'accepted': True})

    await HttpNotifications(client(handler), 'secret').send(event_id, {'template_code': 'x'}, 'req-booking')

    assert seen == {'body': {'event_id': str(event_id), 'template_code': 'x'}, 'request_id': 'req-booking'}


async def test_notifications_rejection_and_wrong_secret():
    """422 — отказ по существу (событие снимут); 401 — неверный секрет стенда, событие надо сохранить."""
    rejecting = HttpNotifications(client(lambda _: httpx.Response(422, json={'detail': []})), 's')
    unauthorized = HttpNotifications(client(lambda _: httpx.Response(401, json={'code': 'x'})), 's')

    with pytest.raises(EventRejectedError):
        await rejecting.send(uuid4(), {}, '-')
    with pytest.raises(StorageUnavailableError):
        await unauthorized.send(uuid4(), {}, '-')
