"""Приём пачки событий: что уходит в очередь и что отклоняется."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from services.collector import INVALID_EVENT, EventCollector
from storage.base import QueueUnavailableError
from tests.unit.conftest import SESSION_ID
from tests.unit.fakes import InMemoryEventQueue

RECEIVED_AT = datetime(2026, 9, 21, 16, 5, 0, tzinfo=UTC)


@pytest.fixture
def collector(queue: InMemoryEventQueue) -> EventCollector:
    return EventCollector(queue, clock=lambda: RECEIVED_AT)


def published_payloads(queue: InMemoryEventQueue) -> list[dict]:
    return [json.loads(message.value) for message in queue.published]


def test_valid_events_are_published(collector, queue, click_event, page_view_event, user_id) -> None:
    """Проверенные события уходят в очередь, а метод отчитывается об их числе."""
    result = collector.collect([click_event, page_view_event], user_id=user_id)

    assert result.accepted == 2
    assert result.rejected == []
    assert len(queue.published) == 2


def test_user_id_is_taken_from_token(collector, queue, click_event, user_id) -> None:
    """В сообщение попадает пользователь из токена, а не из тела запроса."""
    collector.collect([click_event], user_id=user_id)

    assert published_payloads(queue)[0]['user_id'] == str(user_id)


def test_received_at_is_added(collector, queue, click_event, user_id) -> None:
    """Сервис проставляет время приёма: по нему видно, насколько врут часы клиента."""
    collector.collect([click_event], user_id=user_id)

    assert published_payloads(queue)[0]['received_at'] == RECEIVED_AT.isoformat()


def test_partition_key_is_the_viewing_session(collector, queue, click_event, user_id) -> None:
    """Ключ сообщения — сессия просмотра: её события приходят потребителю по порядку."""
    collector.collect([click_event], user_id=user_id)

    assert queue.published[0].key == str(SESSION_ID)


def test_event_id_from_the_client_is_kept(collector, queue, page_view_event, user_id) -> None:
    """Идентификатор клиента доезжает до очереди неизменным: по нему аналитика убирает повторы."""
    collector.collect([page_view_event], user_id=user_id)

    assert published_payloads(queue)[0]['event_id'] == page_view_event['event_id']


def test_event_without_event_id_is_rejected(collector, queue, page_view_event, user_id) -> None:
    """Событие без идентификатора не принимается: повтор запроса иначе стал бы новым событием."""
    del page_view_event['event_id']

    result = collector.collect([page_view_event], user_id=user_id)

    assert result.accepted == 0
    assert queue.published == []


def test_broken_event_does_not_take_the_batch_down(collector, queue, click_event, user_id) -> None:
    """Событие, не прошедшее контракт, отклоняется в одиночку: остальные принимаются."""
    result = collector.collect([click_event, {'event_type': 'unknown'}], user_id=user_id)

    assert result.accepted == 1
    assert len(result.rejected) == 1
    assert result.rejected[0].index == 1
    assert result.rejected[0].code == INVALID_EVENT
    assert len(queue.published) == 1


def test_nothing_is_published_when_all_events_are_broken(collector, queue, user_id) -> None:
    """Если ни одно событие не прошло проверку, в очередь не уходит ничего."""
    result = collector.collect([{'event_type': 'unknown'}, {}], user_id=user_id)

    assert result.accepted == 0
    assert len(result.rejected) == 2
    assert queue.published == []


def test_rejection_reason_names_the_field(collector, user_id) -> None:
    """В причине отказа названо поле, из-за которого событие не принято."""
    broken = {
        'event_type': 'page_view',
        'event_id': str(uuid4()),
        'session_id': str(SESSION_ID),
        'occurred_at': '2026-09-21T19:05:02+03:00',
        'client': {'platform': 'web'},
        'page': '/',
        'duration_ms': -5,
    }

    result = collector.collect([broken], user_id=user_id)

    assert 'duration_ms' in result.rejected[0].detail


def test_queue_failure_is_reported_to_the_caller(click_event, user_id) -> None:
    """Недоступность очереди выходит наружу: клиенту нужно ответить отказом, а не «принято»."""
    collector = EventCollector(InMemoryEventQueue(available=False))

    with pytest.raises(QueueUnavailableError):
        collector.collect([click_event], user_id=user_id)


def test_published_payload_keeps_event_fields(collector, queue, click_event, user_id) -> None:
    """Поля события доезжают до очереди без потерь."""
    collector.collect([click_event], user_id=user_id)

    payload = published_payloads(queue)[0]
    assert payload['event_type'] == 'click'
    assert payload['element_type'] == 'film_card'
    assert payload['page'] == '/catalog/drama'
    assert payload['client']['platform'] == 'web'


def test_events_of_different_users_are_not_mixed(collector, queue, click_event) -> None:
    """Каждая пачка помечается своим пользователем."""
    first, second = uuid4(), uuid4()

    collector.collect([click_event], user_id=first)
    collector.collect([click_event], user_id=second)

    assert [payload['user_id'] for payload in published_payloads(queue)] == [str(first), str(second)]
