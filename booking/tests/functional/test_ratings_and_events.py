"""Оценки после показа, рейтинги и доставка событий в сервис уведомлений через outbox."""

import time
from datetime import timedelta

from tests.functional.conftest import Viewer, call, events, new_screening, soon_started, stub
from tests.functional.settings import settings


def wait_events(predicate, timeout: float = settings.event_timeout) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matched = [event for event in events() if predicate(event)]
        if matched:
            return matched
        time.sleep(0.3)
    raise AssertionError(f'событие не пришло за {timeout} с; пришли: {events()}')


def test_rating_before_start_is_too_early(host: Viewer, guest: Viewer):
    """409 rating_too_early до начала показа."""
    screening = new_screening(host)
    call('POST', f'/screenings/{screening["id"]}/bookings', guest, json={'seats': 1})

    response = call('POST', f'/screenings/{screening["id"]}/ratings', guest, json={'target_id': host.user_id,
                                                                                   'score': 5})

    assert (response.status_code, response.json()['code']) == (409, 'rating_too_early')


def test_mutual_ratings_after_screening(host: Viewer, guest: Viewer, other: Viewer):
    """После начала: гость оценивает хоста, хост — гостя; повтор — 409; посторонний — 403; чужая цель — 400."""
    screening = soon_started(host, guest)
    path = f'/screenings/{screening["id"]}/ratings'

    by_guest = call('POST', path, guest, json={'target_id': host.user_id, 'score': 5, 'comment': 'Уютно'})
    by_host = call('POST', path, host, json={'target_id': guest.user_id, 'score': 4})
    again = call('POST', path, guest, json={'target_id': host.user_id, 'score': 1})
    outsider = call('POST', path, other, json={'target_id': host.user_id, 'score': 1})
    wrong = call('POST', path, host, json={'target_id': other.user_id, 'score': 1})
    invalid = call('POST', path, guest, json={'target_id': host.user_id, 'score': 6})

    assert (by_guest.status_code, by_guest.json()['target_role']) == (201, 'host')
    assert (by_host.status_code, by_host.json()['target_role']) == (201, 'guest')
    assert (again.status_code, again.json()['code']) == (409, 'already_rated')
    assert (outsider.status_code, outsider.json()['code']) == (403, 'not_participant')
    assert (wrong.status_code, wrong.json()['code']) == (400, 'invalid_rating_target')
    assert invalid.status_code == 422

    rating = call('GET', f'/users/{host.user_id}/rating').json()
    reviews = call('GET', f'/users/{host.user_id}/reviews', params={'role': 'host'}).json()
    mine = call('GET', f'{path}/mine', guest).json()
    assert (rating['name'], rating['as_host'], rating['as_guest']['votes']) == (
        'Нео Андерсон', {'average': 5.0, 'votes': 1}, 0,
    )
    assert [(r['author_name'], r['comment']) for r in reviews['items']] == [('Тринити Ноль', 'Уютно')]
    assert [r['target_id'] for r in mine] == [host.user_id]


def test_booking_events_reach_notifications(host: Viewer, guest: Viewer):
    """Бронь рождает письма гостю и хосту: событие доходит до сервиса уведомлений с event_id и контекстом."""
    screening = new_screening(host)
    call('POST', f'/screenings/{screening["id"]}/bookings', guest, json={'seats': 2})

    delivered = wait_events(lambda e: e['routing_key'] == 'booking-reporting.v1.created')
    time.sleep(1)

    created = [e for e in events() if e['routing_key'] == 'booking-reporting.v1.created']
    assert {e['template_code'] for e in created} == {'booking_confirmed', 'booking_host_update'}
    assert {e['audience']['user_ids'][0] for e in created} == {guest.user_id, host.user_id}
    context = delivered[0]['context']
    assert (context['film_title'], context['seats'], context['guest_name']) == ('Star Wars', 2, 'Тринити Ноль')
    assert len({e['event_id'] for e in created}) == 2


def test_events_wait_while_notifications_are_down(host: Viewer, guest: Viewer):
    """Сервис уведомлений лежит — бронь проходит, событие ждёт в outbox и уходит после подъёма."""
    stub('POST', '/_down')
    screening = new_screening(host, delta=timedelta(days=3))

    booked = call('POST', f'/screenings/{screening["id"]}/bookings', guest, json={'seats': 1})
    time.sleep(2)
    assert booked.status_code == 201
    assert events() == []

    stub('POST', '/_up')
    wait_events(lambda e: e['audience']['user_ids'] == [guest.user_id])


def test_cancelled_screening_notifies_guests(host: Viewer, guest: Viewer, other: Viewer):
    """Отмена показа — одно событие на всех гостей."""
    screening = new_screening(host)
    for viewer in (guest, other):
        call('POST', f'/screenings/{screening["id"]}/bookings', viewer, json={'seats': 1})

    call('POST', f'/screenings/{screening["id"]}/cancel', host)

    [event] = wait_events(lambda e: e['routing_key'] == 'screening-reporting.v1.cancelled')
    assert sorted(event['audience']['user_ids']) == sorted([guest.user_id, other.user_id])
