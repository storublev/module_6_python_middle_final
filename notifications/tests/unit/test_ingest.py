"""Приём событий: идемпотентность и порядок «сначала запись, потом очередь»."""

from uuid import uuid4

import pytest

from services.ingest import IngestService
from storage.base import StorageUnavailableError
from storage.rabbit import STAGE_PLAN
from tests.unit.fakes import FakeEventStore, FakePublisher, event


async def test_accepted_event_goes_to_queue(ingest: IngestService, publisher: FakePublisher) -> None:
    """Принятое событие попадает в очередь планировщика."""
    accepted = await ingest.accept(event())

    assert accepted.accepted is True
    assert len(publisher.of(STAGE_PLAN)) == 1


async def test_repeated_event_id_does_not_create_second_message(
    ingest: IngestService, publisher: FakePublisher,
) -> None:
    """Повтор запроса с тем же event_id не создаёт второго уведомления.

    Это ФТ-2: отправитель, потерявший ответ, повторяет запрос — и не должен
    получить два письма вместо одного.
    """
    same = event()

    first = await ingest.accept(same)
    second = await ingest.accept(same)

    assert first.accepted is True
    assert second.accepted is False
    assert len(publisher.of(STAGE_PLAN)) == 1


async def test_different_events_are_independent(ingest: IngestService, publisher: FakePublisher) -> None:
    """Разные события с одинаковым содержимым, но разными event_id, проходят оба."""
    await ingest.accept(event(event_id=uuid4()))
    await ingest.accept(event(event_id=uuid4()))

    assert len(publisher.of(STAGE_PLAN)) == 2


async def test_queue_failure_does_not_hide_behind_success(
    events: FakeEventStore, publisher: FakePublisher,
) -> None:
    """Отказ брокера доходит до вызывающего, а не превращается в «принято».

    Иначе API ответил бы 202 на событие, которого в очереди нет, и письмо
    никогда бы не ушло.
    """
    publisher.fail_with = StorageUnavailableError('брокер лежит')
    ingest = IngestService(events, publisher)

    with pytest.raises(StorageUnavailableError):
        await ingest.accept(event())


async def test_event_payload_carries_ids_not_content(ingest: IngestService, publisher: FakePublisher) -> None:
    """В очередь едут идентификаторы и версия, а не готовое письмо.

    Это правило гибридной схемы: содержимое собирает воркер, иначе сообщение
    раздувается и данные протухают ещё в очереди.
    """
    await ingest.accept(event(content_id='series-42', content_version=8, template_code='new_episode'))

    payload = publisher.of(STAGE_PLAN)[0]
    assert payload['content_id'] == 'series-42'
    assert payload['content_version'] == 8
    assert payload['template_code'] == 'new_episode'
    assert 'body' not in payload
    assert 'subject' not in payload
