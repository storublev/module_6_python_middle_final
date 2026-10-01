"""Отправитель: защита от дублей и поведение при отказах канала."""

from uuid import uuid4

import pytest

from channels.base import ChannelUnavailableError
from models.enums import Channel, DeliveryStatus
from services.messages import SendMessage
from services.quiet_hours import QuietHours
from services.sender import DeliveryInProgressError, SenderService
from tests.unit.conftest import SEND_LEASE
from tests.unit.fakes import Database, FakeChannel, FakeDeliveryRepository, FakeOutbox


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
    """Временный отказ канала снимает аренду, оставляя письмо в истории с причиной.

    Без снятия аренды повтор ждал бы её конца, а запись с причиной видна в
    личном кабинете и при разборе.
    """
    channel.unavailable_times = 1
    message = send_message()

    with pytest.raises(ChannelUnavailableError):
        await sender.send(message)

    delivery = db.deliveries[message.idempotency_key]
    assert delivery.status is DeliveryStatus.PENDING
    assert delivery.error == 'Почтовый сервер не отвечает'
    assert message.idempotency_key not in db.delivery_leases


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
    deliveries_repo: FakeDeliveryRepository, notifications_repo, subscriptions_repo, db: Database,
) -> None:
    """Сообщение в неподключённый канал не теряется молча, а попадает в историю с ошибкой."""
    sender = SenderService(
        deliveries_repo, notifications_repo, {}, SEND_LEASE,
        subscriptions_repo, FakeOutbox(db), QuietHours(0, 0, 'Europe/Moscow'),
    )
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


async def test_crash_before_sending_does_not_lose_the_letter(
    sender: SenderService, deliveries_repo: FakeDeliveryRepository, channel: FakeChannel,
) -> None:
    """Отправитель упал между захватом и отправкой — письмо уходит после конца аренды.

    Раньше брошенная запись `PENDING` считалась «уже отправлено»: после
    перезапуска письмо пропускалось, а сообщение подтверждалось брокеру.
    """
    from datetime import datetime, timezone

    from models.notification import RenderedMessage

    message = send_message()
    start = datetime.now(timezone.utc)
    # Упавший отправитель успел только забрать письмо.
    await deliveries_repo.claim(RenderedMessage(**message.model_dump(exclude={'content_id', 'content_version'})),
                                SEND_LEASE, start)

    sent = await sender.send(message, now=start + SEND_LEASE)

    assert sent is True
    assert len(channel.sent) == 1


async def test_letter_in_flight_is_retried_not_acknowledged(
    sender: SenderService, deliveries_repo: FakeDeliveryRepository, channel: FakeChannel,
) -> None:
    """Пока чужая аренда идёт, повтор не отправляет письмо и не считает его отправленным.

    Он поднимает ошибку, и сообщение уходит в отложенный повтор: если
    держатель упадёт, письмо заберут позже, а не потеряют.
    """
    from datetime import datetime, timezone

    from models.notification import RenderedMessage

    message = send_message()
    start = datetime.now(timezone.utc)
    await deliveries_repo.claim(RenderedMessage(**message.model_dump(exclude={'content_id', 'content_version'})),
                                SEND_LEASE, start)

    with pytest.raises(DeliveryInProgressError):
        await sender.send(message, now=start + SEND_LEASE / 2)
    assert channel.sent == []


async def test_only_one_sender_takes_over_an_abandoned_letter(
    deliveries_repo: FakeDeliveryRepository,
) -> None:
    """Брошенное письмо забирает ровно один отправитель, второй видит занятую аренду."""
    from datetime import datetime, timezone

    from models.enums import ClaimState
    from models.notification import RenderedMessage

    rendered = RenderedMessage(**send_message().model_dump(exclude={'content_id', 'content_version'}))
    start = datetime.now(timezone.utc)
    await deliveries_repo.claim(rendered, SEND_LEASE, start)

    first = await deliveries_repo.claim(rendered, SEND_LEASE, start + SEND_LEASE)
    second = await deliveries_repo.claim(rendered, SEND_LEASE, start + SEND_LEASE)

    assert first.state is ClaimState.CLAIMED
    assert first.recovered is True
    assert second.state is ClaimState.BUSY


async def test_letter_is_cancelled_if_viewer_unsubscribed_while_it_waited(
    sender: SenderService, subscriptions_repo, db: Database, channel: FakeChannel,
) -> None:
    """Отписка, случившаяся пока письмо ждало в очереди, отменяет письмо.

    Раньше подписка проверялась только при выборе получателей, и готовое
    письмо уходило уже отписавшемуся зрителю.
    """
    message = send_message()
    await subscriptions_repo.set_enabled(message.user_id, message.template_code, Channel.EMAIL, enabled=False)

    sent = await sender.send(message)

    assert sent is False
    assert channel.sent == []
    assert db.deliveries[message.idempotency_key].status is DeliveryStatus.SKIPPED


async def test_global_unsubscribe_also_cancels_waiting_letter(
    sender: SenderService, subscriptions_repo, db: Database, channel: FakeChannel,
) -> None:
    """Отписка от всего по ссылке из письма тоже отменяет письмо, ждущее отправки."""
    message = send_message()
    await subscriptions_repo.unsubscribe_all(message.user_id)

    assert await sender.send(message) is False
    assert channel.sent == []


async def test_cancelled_letter_is_not_sent_after_resubscribing(
    sender: SenderService, subscriptions_repo, channel: FakeChannel,
) -> None:
    """Отменённое письмо не воскресает, если зритель потом подписался снова."""
    message = send_message()
    await subscriptions_repo.set_enabled(message.user_id, message.template_code, Channel.EMAIL, enabled=False)
    await sender.send(message)
    await subscriptions_repo.set_enabled(message.user_id, message.template_code, Channel.EMAIL, enabled=True)

    assert await sender.send(message) is False
    assert channel.sent == []


def night_sender(
    deliveries_repo: FakeDeliveryRepository, notifications_repo, subscriptions_repo, channel: FakeChannel, db: Database,
) -> SenderService:
    return SenderService(
        deliveries_repo, notifications_repo, {channel.channel.value: channel}, SEND_LEASE,
        subscriptions_repo, FakeOutbox(db), QuietHours(21, 9, 'Europe/Moscow'),
    )


async def test_letter_is_postponed_if_night_came_while_it_waited(
    deliveries_repo: FakeDeliveryRepository, notifications_repo, subscriptions_repo,
    channel: FakeChannel, db: Database,
) -> None:
    """Письмо, дождавшееся отправки ночью по времени зрителя, откладывается до утра.

    Собрано днём, а очередь разобралась ночью — раньше оно уходило будить
    зрителя.
    """
    from datetime import datetime, timezone

    from storage.rabbit import STAGE_SEND

    sender = night_sender(deliveries_repo, notifications_repo, subscriptions_repo, channel, db)
    message = send_message(timezone='Asia/Vladivostok')
    # 15:00 UTC — 01:00 во Владивостоке.
    night = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)

    sent = await sender.send(message, now=night)

    assert sent is False
    assert channel.sent == []
    [task] = db.outbox.values()
    assert task['publication'].stage == STAGE_SEND
    # 09:00 во Владивостоке — 23:00 UTC того же дня.
    assert task['available_at'] == datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc)
    # Аренда снята: утром письмо заберёт любой отправитель.
    assert message.idempotency_key not in db.delivery_leases
    assert db.deliveries[message.idempotency_key].status is DeliveryStatus.PENDING


async def test_postponed_letter_is_sent_in_the_morning(
    deliveries_repo: FakeDeliveryRepository, notifications_repo, subscriptions_repo,
    channel: FakeChannel, db: Database,
) -> None:
    """Утром отложенное письмо уходит — тем же ключом, без дубля."""
    from datetime import datetime, timezone

    sender = night_sender(deliveries_repo, notifications_repo, subscriptions_repo, channel, db)
    message = send_message(timezone='Asia/Vladivostok')
    await sender.send(message, now=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc))

    sent = await sender.send(message, now=datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc))

    assert sent is True
    assert len(channel.sent) == 1
