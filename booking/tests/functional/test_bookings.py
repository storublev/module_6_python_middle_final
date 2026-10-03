"""Эндпоинты броней и главная гарантия: не больше мест, чем есть у хоста."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from tests.functional.conftest import Viewer, call, new_screening, register


def book(viewer: Viewer, screening: dict, seats: int = 1):
    return call('POST', f'/screenings/{screening["id"]}/bookings', viewer, json={'seats': seats})


def test_book_seats(host: Viewer, guest: Viewer):
    """201: бронь с именем гостя из справочника; места показа уменьшились."""
    screening = new_screening(host, capacity=4)

    response = book(guest, screening, 3)

    assert response.status_code == 201
    assert (response.json()['guest_name'], response.json()['seats']) == ('Тринити Ноль', 3)
    assert call('GET', f'/screenings/{screening["id"]}').json()['seats_left'] == 1


def test_concurrent_guests_never_oversell(host: Viewer):
    """30 гостей одновременно на 5 мест: продано ровно 5, остальным — 409 not_enough_seats."""
    screening = new_screening(host, capacity=5)
    guests = [register(f'Гость{n}', 'Тестовый') for n in range(30)]

    with ThreadPoolExecutor(max_workers=30) as pool:
        responses = list(pool.map(lambda viewer: book(viewer, screening), guests))

    statuses = sorted(response.status_code for response in responses)
    assert statuses.count(201) == 5
    assert {r.json()['code'] for r in responses if r.status_code != 201} == {'not_enough_seats'}
    shown = call('GET', f'/screenings/{screening["id"]}').json()
    assert (shown['seats_taken'], shown['seats_left']) == (5, 0)


@pytest.mark.parametrize(
    'seats, status, code',
    [
        pytest.param(0, 400, 'seats_out_of_range', id='zero'),
        pytest.param(11, 400, 'seats_out_of_range', id='too-many-at-once'),
        pytest.param(5, 409, 'not_enough_seats', id='more-than-left'),
    ],
)
def test_book_errors(host: Viewer, guest: Viewer, seats, status, code):
    """Число мест вне правил и больше оставшегося — понятный код ошибки."""
    screening = new_screening(host, capacity=4)

    response = book(guest, screening, seats)

    assert (response.status_code, response.json()['code']) == (status, code)


def test_book_conflicts(host: Viewer, guest: Viewer):
    """403 — хост у себя; 409 — вторая бронь; 404 — нет показа; 401 — без входа."""
    screening = new_screening(host)
    book(guest, screening)

    own = book(host, screening)
    second = book(guest, screening)
    missing = call('POST', f'/screenings/{uuid.uuid4()}/bookings', guest, json={'seats': 1})
    anonymous = call('POST', f'/screenings/{screening["id"]}/bookings', json={'seats': 1})

    assert (own.status_code, own.json()['code']) == (403, 'own_screening')
    assert (second.status_code, second.json()['code']) == (409, 'already_booked')
    assert (missing.status_code, missing.json()['code']) == (404, 'screening_not_found')
    assert anonymous.status_code == 401


def test_change_and_cancel_booking(host: Viewer, guest: Viewer, other: Viewer):
    """PATCH меняет места в пределах свободных; cancel возвращает их; повторная отмена — 409."""
    screening = new_screening(host, capacity=4)
    booking = book(guest, screening, 1).json()
    book(other, screening, 2)
    path = f'/bookings/{booking["id"]}'

    grown = call('PATCH', path, guest, json={'seats': 2})
    too_many = call('PATCH', path, guest, json={'seats': 3})
    same = call('PATCH', path, guest, json={'seats': 2})
    foreign = call('PATCH', path, other, json={'seats': 1})
    cancelled = call('POST', f'{path}/cancel', guest)
    again = call('POST', f'{path}/cancel', guest)

    assert (grown.status_code, grown.json()['seats']) == (200, 2)
    assert (too_many.status_code, too_many.json()['code']) == (409, 'not_enough_seats')
    assert (same.status_code, same.json()['code']) == (400, 'nothing_to_change')
    assert (foreign.status_code, foreign.json()['code']) == (403, 'not_booking_owner')
    assert (cancelled.status_code, cancelled.json()['status']) == (200, 'cancelled')
    assert (again.status_code, again.json()['code']) == (409, 'booking_cancelled')
    assert call('GET', f'/screenings/{screening["id"]}').json()['seats_taken'] == 2
    assert call('POST', f'/bookings/{uuid.uuid4()}/cancel', guest).json()['code'] == 'booking_not_found'


def test_my_booking_and_my_bookings(host: Viewer, guest: Viewer):
    """Бронь зрителя на показе и «Мои брони» с показом целиком."""
    screening = new_screening(host, delta=timedelta(days=1))
    booking = book(guest, screening, 2).json()

    mine = call('GET', f'/screenings/{screening["id"]}/bookings/mine', guest)
    listing = call('GET', '/me/bookings', guest).json()

    assert mine.json()['id'] == booking['id']
    assert [(item['booking']['id'], item['screening']['film_title']) for item in listing['items']] == [
        (booking['id'], 'Star Wars'),
    ]
    assert call('GET', '/me/bookings').status_code == 401
