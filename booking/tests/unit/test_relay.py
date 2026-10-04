"""Ретранслятор outbox: события о бронях уходят в сервис уведомлений ровно с тем event_id, что в базе."""

from datetime import UTC, datetime, timedelta

from models.domain import OutboxDraft
from services.relay import OutboxRelay
from storage.base import EventRejectedError, StorageUnavailableError
from tests.unit.fakes import Database, FakeGateway, FakeOutbox

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
LEASE = timedelta(seconds=30)
RETRY = timedelta(seconds=60)


async def outbox_with(count: int) -> FakeOutbox:
    outbox = FakeOutbox(Database())
    await outbox.add([OutboxDraft(payload={'n': n}, request_id=f'req-{n}') for n in range(count)])
    for entry in outbox.db.outbox.values():
        entry.available_at = NOW - timedelta(seconds=1)
    return outbox


async def test_sent_events_are_removed():
    """Отправленное событие удаляется; event_id — идентификатор строки outbox, request_id — тот, что был у брони."""
    outbox, gateway = await outbox_with(2), FakeGateway()
    ids = set(outbox.db.outbox)

    sent = await OutboxRelay(outbox, gateway, 10, LEASE, RETRY).relay_once(NOW)

    assert sent == 2
    assert outbox.db.outbox == {}
    assert {event_id for event_id, _, _ in gateway.sent} == ids
    assert {request_id for _, _, request_id in gateway.sent} == {'req-0', 'req-1'}


async def test_unavailable_service_postpones_events():
    """Сервис уведомлений лежит — событие ждёт паузу повтора, а не теряется."""
    outbox, gateway = await outbox_with(1), FakeGateway()
    gateway.failure = StorageUnavailableError('connection refused')

    sent = await OutboxRelay(outbox, gateway, 10, LEASE, RETRY).relay_once(NOW)

    entry = next(iter(outbox.db.outbox.values()))
    assert sent == 0
    assert (entry.available_at, entry.attempts, entry.last_error) == (NOW + RETRY, 1, 'connection refused')


async def test_rejected_event_is_dropped():
    """Отказ по существу (4xx) повтором не лечится: событие снимается, остальные идут дальше."""
    outbox, gateway = await outbox_with(2), FakeGateway()
    gateway.failure = EventRejectedError('422: template not found')

    sent = await OutboxRelay(outbox, gateway, 10, LEASE, RETRY).relay_once(NOW)

    assert (sent, outbox.db.outbox) == (0, {})


async def test_batch_size_is_respected():
    """За проход уходит не больше пачки, остальное — в следующий."""
    outbox, gateway = await outbox_with(5), FakeGateway()
    relay = OutboxRelay(outbox, gateway, 2, LEASE, RETRY)

    assert [await relay.relay_once(NOW) for _ in range(4)] == [2, 2, 1, 0]


async def test_events_in_lease_are_not_resent():
    """Событие, взятое другим ретранслятором, не уходит второй раз, пока идёт аренда."""
    outbox, gateway = await outbox_with(1), FakeGateway()
    gateway.failure = StorageUnavailableError('timeout')
    relay = OutboxRelay(outbox, gateway, 10, LEASE, RETRY)
    await relay.relay_once(NOW)
    gateway.failure = None

    assert await relay.relay_once(NOW + RETRY - timedelta(seconds=1)) == 0
    assert await relay.relay_once(NOW + RETRY) == 1
