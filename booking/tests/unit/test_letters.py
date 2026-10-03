"""События для писем: формат, который принимает сервис уведомлений."""

from datetime import UTC, datetime
from uuid import uuid4

from models.domain import Booking, BookingStatus, Screening, ScreeningStatus
from services.letters import Letters

STARTS = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)
HOST, GUEST = uuid4(), uuid4()
SCREENING = Screening(
    id=uuid4(), host_id=HOST, host_name='Нео', film_id=uuid4(), film_title='Star Wars', film_poster=None,
    starts_at=STARTS, place='Зал 3', address='Новый Арбат, 24', description=None, capacity=6, seats_taken=2,
    status=ScreeningStatus.SCHEDULED, created_at=STARTS, updated_at=STARTS,
)
BOOKING = Booking(
    id=uuid4(), screening_id=SCREENING.id, guest_id=GUEST, guest_name='Тринити', seats=2,
    status=BookingStatus.ACTIVE, created_at=STARTS, updated_at=STARTS,
)
LETTERS = Letters('https://practix.local/', 'Europe/Moscow')


def test_event_matches_notification_contract():
    """Ключ маршрутизации по правилу сервиса уведомлений, адресаты — по user_id, без адресов почты."""
    guest_event, host_event = LETTERS.booking_created(SCREENING, BOOKING)

    assert guest_event.payload['routing_key'] == 'booking-reporting.v1.created'
    assert guest_event.payload['audience'] == {'kind': 'users', 'user_ids': [str(GUEST)]}
    assert host_event.payload['audience']['user_ids'] == [str(HOST)]
    assert 'event_id' not in guest_event.payload
    assert '@' not in str(guest_event.payload)


def test_time_is_local_and_link_points_to_screening():
    """Время в письме — московское с поясом, ссылка ведёт на страницу показа без двойного слеша."""
    context = LETTERS.booking_created(SCREENING, BOOKING)[0].payload['context']

    assert context['starts_at'] == '17.10.2026 19:00 (MSK)'
    assert context['action_url'] == f'https://practix.local/screenings/{SCREENING.id}'
    assert (context['seats'], context['seats_left'], context['change']) == (2, 4, 'created')


def test_no_guests_no_event():
    """Перенос показа без гостей не рождает пустое событие."""
    assert LETTERS.screening_changed(SCREENING, []) == []


def test_guests_are_deduplicated():
    """Каждый гость получает одно письмо, даже если пришёл в списке дважды."""
    other = uuid4()

    [event] = LETTERS.screening_cancelled(SCREENING, [GUEST, other, GUEST])

    assert sorted(event.payload['audience']['user_ids']) == sorted([str(GUEST), str(other)])
