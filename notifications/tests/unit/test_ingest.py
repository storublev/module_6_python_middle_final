"""Приём событий: идемпотентность и outbox вместо публикации из обработчика."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from services.ingest import IngestService
from services.relay import OutboxRelay
from storage.base import StorageUnavailableError
from storage.rabbit import STAGE_PLAN
from tests.unit.fakes import Database, FakeEventStore, FakeOutbox, FakePublisher, event

LEASE = timedelta(seconds=30)
RETRY = timedelta(seconds=60)


def relay(db: Database, publisher: FakePublisher, batch_size: int = 100) -> OutboxRelay:
    return OutboxRelay(FakeOutbox(db), publisher, batch_size, LEASE, RETRY)


async def test_accepted_event_is_queued_with_it(ingest: IngestService, db: Database) -> None:
    """Принятое событие и задание на публикацию появляются вместе."""
    accepted = await ingest.accept(event())

    assert accepted.accepted is True
    assert len(db.events) == 1
    assert len(db.outbox_of(STAGE_PLAN)) == 1


async def test_repeated_event_id_does_not_create_second_message(ingest: IngestService, db: Database) -> None:
    """Повтор запроса с тем же event_id не создаёт второго уведомления.

    Это ФТ-2: отправитель, потерявший ответ, повторяет запрос — и не должен
    получить два письма вместо одного.
    """
    same = event()

    first = await ingest.accept(same)
    second = await ingest.accept(same)

    assert first.accepted is True
    assert second.accepted is False
    assert len(db.outbox_of(STAGE_PLAN)) == 1


async def test_different_events_are_independent(ingest: IngestService, db: Database) -> None:
    """Разные события с одинаковым содержимым, но разными event_id, проходят оба."""
    await ingest.accept(event(event_id=uuid4()))
    await ingest.accept(event(event_id=uuid4()))

    assert len(db.outbox_of(STAGE_PLAN)) == 2


async def test_broker_outage_does_not_lose_the_event(events: FakeEventStore, db: Database) -> None:
    """Лежащий брокер не теряет событие: оно уходит в очередь, когда брокер поднимется.

    Раньше событие записывалось, а публикация падала: повтор запроса получал
    `accepted: false`, а письмо не уходило никогда.
    """
    publisher = FakePublisher()
    publisher.fail_with = StorageUnavailableError('брокер лежит')
    accepted = await IngestService(events).accept(event())
    now = datetime.now(timezone.utc)

    published_while_down = await relay(db, publisher).relay_once(now)

    assert accepted.accepted is True
    assert published_while_down == 0
    assert len(db.outbox_of(STAGE_PLAN)) == 1

    publisher.fail_with = None
    # До конца паузы повтора задание не берётся — брокер не долбят в цикле.
    assert await relay(db, publisher).relay_once(now + RETRY / 2) == 0
    assert await relay(db, publisher).relay_once(now + RETRY) == 1
    assert len(publisher.of(STAGE_PLAN)) == 1
    assert db.outbox == {}


async def test_published_task_is_removed(ingest: IngestService, db: Database) -> None:
    """Опубликованное задание удаляется и второй раз не уходит."""
    publisher = FakePublisher()
    await ingest.accept(event())
    now = datetime.now(timezone.utc)

    await relay(db, publisher).relay_once(now)
    await relay(db, publisher).relay_once(now + LEASE * 2)

    assert len(publisher.of(STAGE_PLAN)) == 1


async def test_claimed_task_is_invisible_until_lease_expires(ingest: IngestService, db: Database) -> None:
    """Пока ретранслятор держит задание, другой его не берёт; после срока аренды — берёт.

    Так переживается падение ретранслятора между публикацией и удалением:
    задание вернётся, а не потеряется.
    """
    await ingest.accept(event())
    outbox = FakeOutbox(db)
    now = datetime.now(timezone.utc)

    first = await outbox.claim(10, LEASE, now)
    second = await outbox.claim(10, LEASE, now + LEASE / 2)
    after_lease = await outbox.claim(10, LEASE, now + LEASE)

    assert len(first) == 1
    assert second == []
    assert [item.id for item in after_lease] == [first[0].id]


async def test_event_payload_carries_ids_not_content(ingest: IngestService, db: Database) -> None:
    """В очередь едут идентификаторы и версия, а не готовое письмо.

    Это правило гибридной схемы: содержимое собирает воркер, иначе сообщение
    раздувается и данные протухают ещё в очереди.
    """
    await ingest.accept(event(content_id='series-42', content_version=8, template_code='new_episode'))

    payload = db.outbox_of(STAGE_PLAN)[0]
    assert payload['content_id'] == 'series-42'
    assert payload['content_version'] == 8
    assert payload['template_code'] == 'new_episode'
    assert 'body' not in payload
    assert 'subject' not in payload
