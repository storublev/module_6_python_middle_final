"""Отправитель: защита от дублей и поведение при отказах канала."""

from uuid import uuid4

import pytest

from channels.base import ChannelUnavailableError
from models.enums import Channel, DeliveryStatus
from services.messages import SendMessage
from services.sender import SenderService
from tests.unit.fakes import Database, FakeChannel, FakeDeliveryRepository


def send_message(**overrides: object) -> SendMessage:
    data: dict = {
        'idempotency_key': 'key-1',
        'user_id': uuid4(),
        'channel': Channel.EMAIL,
        'template_code': 'new_episode',
        'address': 'viewer@example.com',
        'subject': 'Вышла новая серия',
        'body': '<p>Смотрите</p>',
    }
    data.update(overrides)
    return SendMessage(**data)


async def test_message_is_sent_once(sender: SenderService, channel: FakeChannel) -> None:
    """Обычная отправка доходит до канала."""
    sent = await sender.send(send_message())

    assert sent is True
    assert len(channel.sent) == 1


async def test_repeated_delivery_does_not_send_twice(sender: SenderService, channel: FakeChannel) -> None:
    """Повторная доставка того же сообщения не отправляет второе письмо.

    Брокер вправе доставить сообщение повторно (at-least-once), и защита от
    дублей — ключ идемпотентности, а не надежда на брокера.
    """
    message = send_message()

    first = await sender.send(message)
    second = await sender.send(message)

    assert first is True
    assert second is False
    assert len(channel.sent) == 1


async def test_delivery_is_recorded_before_sending(
    sender: SenderService, db: Database, channel: FakeChannel,
) -> None:
    """Факт отправки записывается и получает статус «отправлено»."""
    message = send_message()

    await sender.send(message)

    assert db.deliveries[message.idempotency_key].status is DeliveryStatus.SENT


async def test_temporary_failure_releases_the_key(
    sender: SenderService, db: Database, channel: FakeChannel,
) -> None:
    """Временный отказ канала снимает бронь ключа и поднимается наружу.

    Без снятия брони повтор упёрся бы в собственный ключ, и письмо не ушло бы
    никогда — то есть «ничего не теряется» превратилось бы в «тихо потеряли».
    """
    channel.unavailable_times = 1
    message = send_message()

    with pytest.raises(ChannelUnavailableError):
        await sender.send(message)

    assert message.idempotency_key not in db.deliveries


async def test_message_is_delivered_after_retry(sender: SenderService, channel: FakeChannel) -> None:
    """После временного отказа повтор доводит письмо до адресата."""
    channel.unavailable_times = 1
    message = send_message()

    with pytest.raises(ChannelUnavailableError):
        await sender.send(message)
    sent = await sender.send(message)

    assert sent is True
    assert len(channel.sent) == 1


async def test_permanent_rejection_is_recorded_and_not_retried(
    sender: SenderService, db: Database, channel: FakeChannel,
) -> None:
    """Постоянный отказ (нет такого ящика) записывается как неудача и не повторяется.

    Повторять бессмысленно: сколько ни жди, адрес не станет существующим.
    """
    channel.reject = True
    message = send_message()

    sent = await sender.send(message)

    assert sent is False
    assert db.deliveries[message.idempotency_key].status is DeliveryStatus.FAILED
    assert channel.sent == []


async def test_unknown_channel_is_recorded_as_failure(
    deliveries_repo: FakeDeliveryRepository, notifications_repo, db: Database,
) -> None:
    """Сообщение в неподключённый канал не теряется молча, а попадает в историю с ошибкой."""
    sender = SenderService(deliveries_repo, notifications_repo, {})
    message = send_message(channel=Channel.SMS)

    sent = await sender.send(message)

    assert sent is False
    assert db.deliveries[message.idempotency_key].status is DeliveryStatus.FAILED


async def test_content_version_is_remembered_after_send(
    sender: SenderService, notifications_repo, channel: FakeChannel,
) -> None:
    """После отправки запоминается версия данных, о которой сообщили.

    Это то, что гасит следующее событие о той же серии.
    """
    message = send_message(content_id='series-42', content_version=8)

    await sender.send(message)

    record = await notifications_repo.get(message.user_id, message.template_code, 'series-42')
    assert record is not None
    assert record.content_version == 8


async def test_failed_send_does_not_mark_content_as_notified(
    sender: SenderService, notifications_repo, channel: FakeChannel,
) -> None:
    """Неотправленное письмо не помечает данные как «уже сообщили».

    Иначе после сбоя зритель не узнал бы о новой серии никогда.
    """
    channel.reject = True
    message = send_message(content_id='series-42', content_version=8)

    await sender.send(message)

    assert await notifications_repo.get(message.user_id, message.template_code, 'series-42') is None
