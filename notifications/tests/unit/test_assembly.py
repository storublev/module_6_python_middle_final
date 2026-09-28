"""Сборщик: данные пачкой, окно суток и ключ идемпотентности."""

from datetime import datetime, timezone
from uuid import uuid4

from models.enums import Channel
from services.assembly import AssemblyService, QuietHours, idempotency_key
from services.messages import RenderMessage
from storage.rabbit import STAGE_RENDER, STAGE_SEND
from tests.unit.fakes import Database, FakeContactDirectory, FakePublisher, recipient, template

# Полдень по Москве — заведомо разрешённое время; 03:00 — заведомо тихое.
NOON_MSK = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
NIGHT_MSK = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)


def render_message(user_ids: list, **overrides: object) -> RenderMessage:
    data: dict = {
        'event_id': uuid4(),
        'template_code': 'new_episode',
        'template_version': 1,
        'channel': Channel.EMAIL,
        'user_ids': user_ids,
    }
    data.update(overrides)
    return RenderMessage(**data)


async def test_contacts_are_fetched_once_per_batch(
    assembly: AssemblyService, db: Database, directory: FakeContactDirectory, publisher: FakePublisher,
) -> None:
    """На всю пачку — один запрос в справочник контактов, а не по одному на зрителя.

    Тысяча запросов в сервис авторизации на рассылку и есть тот отказ
    подсистемы данных, от которого предостерегает задача урока про RabbitMQ.
    """
    db.template_versions[('new_episode', 1)] = template('new_episode')
    users = [uuid4() for _ in range(5)]
    directory.recipients = {user_id: recipient(user_id) for user_id in users}

    built = await assembly.assemble(render_message(users), now=NOON_MSK)

    assert built == len(users)
    assert directory.calls == 1


async def test_rendered_message_carries_personal_data(
    assembly: AssemblyService, db: Database, directory: FakeContactDirectory, publisher: FakePublisher,
) -> None:
    """Имя получателя подставляется в письмо — это и есть персонализация."""
    db.template_versions[('new_episode', 1)] = template('new_episode')
    viewer = uuid4()
    directory.recipients = {viewer: recipient(viewer, first_name='Нео', last_name='Андерсон')}

    await assembly.assemble(render_message([viewer]), now=NOON_MSK)

    message = publisher.of(STAGE_SEND)[0]
    assert 'Нео' in message['subject']
    assert 'Нео Андерсон' in message['body']
    assert message['address'] == 'viewer@example.com'


async def test_night_recipients_are_deferred_not_sent(
    assembly: AssemblyService, db: Database, directory: FakeContactDirectory, publisher: FakePublisher,
) -> None:
    """Ночью письмо не уходит: оно возвращается в очередь сборки и ждёт утра.

    Ночная отправка — самая обидная ошибка рассылки, о ней прямо предупреждает
    урок «Как испортить жизнь клиенту».
    """
    db.template_versions[('new_episode', 1)] = template('new_episode')
    viewer = uuid4()
    directory.recipients = {viewer: recipient(viewer, timezone='Europe/Moscow')}

    built = await assembly.assemble(render_message([viewer]), now=NIGHT_MSK)

    assert built == 0
    assert publisher.of(STAGE_SEND) == []
    assert len(publisher.of(STAGE_RENDER)) == 1


async def test_quiet_hours_are_counted_in_viewer_timezone(
    assembly: AssemblyService, db: Database, directory: FakeContactDirectory, publisher: FakePublisher,
) -> None:
    """Окно суток считается в часовом поясе зрителя, а не сервера.

    В один и тот же момент в Москве день, а во Владивостоке уже ночь: письмо
    должно уйти москвичу и подождать у дальневосточника.
    """
    db.template_versions[('new_episode', 1)] = template('new_episode')
    moscow, vladivostok = uuid4(), uuid4()
    directory.recipients = {
        moscow: recipient(moscow, timezone='Europe/Moscow'),
        vladivostok: recipient(vladivostok, timezone='Asia/Vladivostok'),
    }
    # 15:00 в Москве — это 22:00 во Владивостоке.
    moment = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

    built = await assembly.assemble(render_message([moscow, vladivostok]), now=moment)

    assert built == 1
    assert publisher.of(STAGE_SEND)[0]['user_id'] == str(moscow)
    assert publisher.of(STAGE_RENDER)[0]['user_ids'] == [str(vladivostok)]


async def test_unknown_timezone_falls_back_to_default(quiet_hours: QuietHours) -> None:
    """Испорченный часовой пояс не роняет рассылку: берётся пояс по умолчанию.

    Значение приходит из чужого сервиса, и полагаться на его проверки нельзя.
    """
    broken = recipient(timezone='Europe/Atlantis')

    assert quiet_hours.is_quiet(NIGHT_MSK, broken) is True
    assert quiet_hours.is_quiet(NOON_MSK, broken) is False


async def test_missing_template_version_stops_batch(
    assembly: AssemblyService, publisher: FakePublisher,
) -> None:
    """Пропавшая версия шаблона останавливает пачку, а не собирает пустое письмо."""
    built = await assembly.assemble(render_message([uuid4()]), now=NOON_MSK)

    assert built == 0
    assert publisher.of(STAGE_SEND) == []


async def test_broken_template_skips_one_recipient_not_whole_batch(
    assembly: AssemblyService, db: Database, directory: FakeContactDirectory, publisher: FakePublisher,
) -> None:
    """Письмо, которое не собралось, пропускает одного получателя, а не всю пачку.

    Шаблон проверяется при сохранении, но данные конкретного письма могут
    оказаться неожиданными — и это не повод не отправить остальным.
    """
    db.template_versions[('new_episode', 1)] = template(
        'new_episode', subject='{{ first_name }}', body='{{ items | join(", ") }}',
    )
    good, bad = uuid4(), uuid4()
    directory.recipients = {good: recipient(good), bad: recipient(bad)}

    built = await assembly.assemble(
        render_message([good, bad], context={'items': ['Матрица']}), now=NOON_MSK,
    )

    assert built == 2


def test_idempotency_key_is_stable_for_same_event_and_viewer() -> None:
    """Один и тот же ключ у повторной сборки того же события — иначе защита от дублей не работает."""
    message = render_message([uuid4()])
    viewer = uuid4()

    assert idempotency_key(message, viewer) == idempotency_key(message, viewer)


def test_idempotency_key_differs_for_different_viewers() -> None:
    """Разным получателям — разные ключи, иначе письмо уйдёт только одному."""
    message = render_message([uuid4()])

    assert idempotency_key(message, uuid4()) != idempotency_key(message, uuid4())


def test_idempotency_key_differs_for_new_content_version() -> None:
    """Новая версия данных — новый ключ: о девятой серии письмо должно уйти.

    Событие в обоих сообщениях одно и то же намеренно: иначе ключи различались
    бы сами собой и тест ничего бы не сторожил.
    """
    viewer = uuid4()
    event_id = uuid4()
    eighth = render_message([viewer], event_id=event_id, content_id='series-42', content_version=8)
    ninth = render_message([viewer], event_id=event_id, content_id='series-42', content_version=9)

    assert idempotency_key(eighth, viewer) != idempotency_key(ninth, viewer)
