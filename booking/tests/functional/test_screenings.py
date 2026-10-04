"""Эндпоинты показов: каждый ответ каждого эндпоинта."""

import uuid
from datetime import timedelta

import pytest

from tests.functional.conftest import MOVIE_ID, SERIES_ID, Viewer, call, new_screening, starts_in


def test_create_screening(host: Viewer):
    """201: показ со снимком фильма из каталога и именем хоста из сервиса авторизации."""
    screening = new_screening(host, capacity=6, description='Обсуждение после фильма')

    assert (screening['film_title'], screening['host_name'], screening['host_id']) == (
        'Star Wars', 'Нео Андерсон', host.user_id,
    )
    assert (screening['capacity'], screening['seats_left'], screening['status']) == (6, 6, 'scheduled')
    assert screening['film_poster'].startswith('https://')


@pytest.mark.parametrize(
    'fields, status, code',
    [
        pytest.param({'film_id': SERIES_ID}, 400, 'film_not_bookable', id='series'),
        pytest.param({'film_id': str(uuid.uuid4())}, 404, 'film_not_found', id='unknown-film'),
        pytest.param({'capacity': 0}, 400, 'capacity_out_of_range', id='no-seats'),
        pytest.param({'capacity': 51}, 400, 'capacity_out_of_range', id='too-many-seats'),
        pytest.param({'starts_at': starts_in(timedelta(minutes=-1))}, 400, 'starts_too_soon', id='past'),
        pytest.param({'starts_at': starts_in(timedelta(days=400))}, 400, 'starts_too_late', id='too-far'),
    ],
)
def test_create_screening_rules(host: Viewer, fields, status, code):
    """Правила показа — 4xx с машиночитаемым кодом."""
    body = {'film_id': MOVIE_ID, 'starts_at': starts_in(timedelta(days=1)), 'place': 'Зал', 'address': 'Адрес',
            'capacity': 4} | fields

    response = call('POST', '/screenings', host, json=body)

    assert (response.status_code, response.json()['code']) == (status, code)


def test_create_screening_validation(host: Viewer):
    """422: время без пояса и пустое место; присланные значения в ответ не возвращаются."""
    body = {'film_id': MOVIE_ID, 'starts_at': '2099-10-17T19:00:00', 'place': '', 'address': 'Тайный адрес 7',
            'capacity': 4}

    response = call('POST', '/screenings', host, json=body)

    assert response.status_code == 422
    assert 'Тайный адрес' not in response.text


def test_create_screening_requires_token():
    """401 без токена и с чужой подписью."""
    assert call('POST', '/screenings', json={}).json()['code'] == 'not_authenticated'
    response = call('POST', '/screenings', headers={'Authorization': 'Bearer x.y.z'}, json={})
    assert (response.status_code, response.json()['code']) == (401, 'token_invalid')


def test_get_screening(host: Viewer):
    """200 по id, 404 — нет такого, 422 — не UUID."""
    screening = new_screening(host)

    assert call('GET', f'/screenings/{screening["id"]}').json()['id'] == screening['id']
    assert call('GET', f'/screenings/{uuid.uuid4()}').json()['code'] == 'screening_not_found'
    assert call('GET', '/screenings/not-a-uuid').status_code == 422


def test_list_and_hosts_of_film(host: Viewer, other: Viewer):
    """Карточка фильма: хосты фильма, затем даты выбранного хоста — по времени начала."""
    later = new_screening(host, delta=timedelta(days=3))
    sooner = new_screening(host, delta=timedelta(days=1))
    new_screening(other, delta=timedelta(days=2))

    hosts = call('GET', f'/films/{MOVIE_ID}/hosts', params={'page_size': 100}).json()
    dates = call('GET', '/screenings', params={'film_id': MOVIE_ID, 'host_id': host.user_id}).json()

    offer = next(item for item in hosts['items'] if item['host_id'] == host.user_id)
    assert (offer['host_name'], offer['screenings'], offer['seats_left']) == ('Нео Андерсон', 2, 12)
    assert [item['id'] for item in dates['items']] == [sooner['id'], later['id']]
    assert dates['total'] == 2


def test_list_validation():
    """422 на неверную страницу."""
    assert call('GET', '/screenings', params={'page_size': 1000}).status_code == 422
    assert call('GET', '/screenings', params={'page_number': 0}).status_code == 422


def test_update_screening(host: Viewer, guest: Viewer, other: Viewer):
    """200 — хост меняет место; 403 — чужой; 400 — пустой запрос; 409 — мест меньше забронированного."""
    screening = new_screening(host, capacity=4)
    call('POST', f'/screenings/{screening["id"]}/bookings', guest, json={'seats': 3})
    path = f'/screenings/{screening["id"]}'

    updated = call('PATCH', path, host, json={'place': 'Зал 5'})
    foreign = call('PATCH', path, other, json={'place': 'Мой зал'})
    empty = call('PATCH', path, host, json={})
    shrink = call('PATCH', path, host, json={'capacity': 2})
    missing = call('PATCH', f'/screenings/{uuid.uuid4()}', host, json={'place': 'Зал'})

    assert (updated.status_code, updated.json()['place']) == (200, 'Зал 5')
    assert (foreign.status_code, foreign.json()['code']) == (403, 'not_screening_host')
    assert (empty.status_code, empty.json()['code']) == (400, 'nothing_to_change')
    assert (shrink.status_code, shrink.json()['code']) == (409, 'capacity_below_booked')
    assert (missing.status_code, missing.json()['code']) == (404, 'screening_not_found')


def test_cancel_screening(host: Viewer, guest: Viewer, other: Viewer):
    """200 — отмена с бронями; 409 — повторно; 403 — чужой показ."""
    screening = new_screening(host)
    call('POST', f'/screenings/{screening["id"]}/bookings', guest, json={'seats': 1})
    path = f'/screenings/{screening["id"]}/cancel'

    foreign = call('POST', path, other)
    cancelled = call('POST', path, host)
    again = call('POST', path, host)
    mine = call('GET', f'/screenings/{screening["id"]}/bookings/mine', guest)

    assert (foreign.status_code, foreign.json()['code']) == (403, 'not_screening_host')
    assert (cancelled.status_code, cancelled.json()['status']) == (200, 'cancelled')
    assert (again.status_code, again.json()['code']) == (409, 'screening_closed')
    assert mine.json()['code'] == 'booking_not_found'


def test_guests_are_for_host_only(host: Viewer, guest: Viewer):
    """200 — хост видит гостей с именами и рейтингом; 403 — гость не видит чужих."""
    screening = new_screening(host)
    call('POST', f'/screenings/{screening["id"]}/bookings', guest, json={'seats': 2})

    guests = call('GET', f'/screenings/{screening["id"]}/bookings', host)
    foreign = call('GET', f'/screenings/{screening["id"]}/bookings', guest)

    assert [(g['booking']['guest_name'], g['booking']['seats']) for g in guests.json()] == [('Тринити Ноль', 2)]
    assert guests.json()[0]['rating'] == {'average': None, 'votes': 0}
    assert (foreign.status_code, foreign.json()['code']) == (403, 'not_screening_host')


def test_my_schedule(host: Viewer):
    """Расписание хоста: будущие показы; 401 без токена."""
    screening = new_screening(host)

    upcoming = call('GET', '/me/screenings', host, params={'period': 'upcoming'}).json()
    past = call('GET', '/me/screenings', host, params={'period': 'past'}).json()

    assert [item['id'] for item in upcoming['items']] == [screening['id']]
    assert past['items'] == []
    assert call('GET', '/me/screenings').status_code == 401
    assert call('GET', '/me/screenings', host, params={'period': 'someday'}).status_code == 422
