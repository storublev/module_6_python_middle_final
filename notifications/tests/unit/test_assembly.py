"""Сборщик: данные пачкой, окно суток и ключ идемпотентности."""

from datetime import datetime, timezone
from uuid import uuid4

from models.enums import Channel
from services.assembly import AssemblyService, idempotency_key
from services.messages import RenderMessage
from services.quiet_hours import QuietHours
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
    """Ночью письмо не уходит: оно ждёт утра в базе, а не в очереди сборки.

    Ночная отправка — самая обидная ошибка рассылки, о ней прямо предупреждает
    урок «Как испортить жизнь клиенту».
    """
    db.template_versions[('new_episode', 1)] = template('new_episode')
    viewer = uuid4()
    directory.recipients = {viewer: recipient(viewer, timezone='Europe/Moscow')}

    built = await assembly.assemble(render_message([viewer]), now=NIGHT_MSK)

    assert built == 0
    assert publisher.of(STAGE_SEND) == []
    # В очередь сборки сразу ничего не возвращается — иначе письмо ходило бы
    # по кругу до утра.
    assert publisher.of(STAGE_RENDER) == []
    assert db.outbox_of(STAGE_RENDER)[0]['user_ids'] == [str(viewer)]


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
    assert db.outbox_of(STAGE_RENDER)[0]['user_ids'] == [str(vladivostok)]


async def test_unknown_timezone_falls_back_to_default(quiet_hours: QuietHours) -> None:
    """Испорченный часовой пояс не роняет рассылку: берётся пояс по умолчанию.

    Значение приходит из чужого сервиса, и полагаться на его проверки нельзя.
    """
    assert quiet_hours.is_quiet(NIGHT_MSK, 'Europe/Atlantis') is True
    assert quiet_hours.is_quiet(NOON_MSK, 'Europe/Atlantis') is False


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


async def test_unsubscribe_link_is_personal_and_signed(
    assembly: AssemblyService, db: Database, directory: FakeContactDirectory, publisher: FakePublisher,
) -> None:
    """Ссылка отписки в письме содержит идентификатор зрителя и подпись.

    Без них переход из письма упирается в проверку параметров и отписаться
    нельзя. Общая ссылка на всех — это, по сути, её отсутствие.
    """
    db.template_versions[('new_episode', 1)] = template(
        'new_episode', subject='Привет', body='<a href="{{ unsubscribe_url }}">Отписаться</a>',
    )
    viewer = uuid4()
    directory.recipients = {viewer: recipient(viewer)}

    await assembly.assemble(render_message([viewer]), now=NOON_MSK)

    body = publisher.of(STAGE_SEND)[0]['body']
    assert f'user_id={viewer}' in body
    assert 'token=' in body
    assert body.count('&amp;') == 1


async def test_unsubscribe_links_differ_between_viewers(
    assembly: AssemblyService, db: Database, directory: FakeContactDirectory, publisher: FakePublisher,
) -> None:
    """У двух зрителей ссылки отписки разные: по чужой отписать нельзя."""
    db.template_versions[('new_episode', 1)] = template(
        'new_episode', subject='Привет', body='{{ unsubscribe_url }}',
    )
    first, second = uuid4(), uuid4()
    directory.recipients = {first: recipient(first), second: recipient(second)}

    await assembly.assemble(render_message([first, second]), now=NOON_MSK)

    links = {message['body'] for message in publisher.of(STAGE_SEND)}
    assert len(links) == 2


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


async def test_night_letter_returns_to_work_only_in_the_morning(
    assembly: AssemblyService, db: Database, directory: FakeContactDirectory,
) -> None:
    """Отложенное письмо возвращается в работу в 9:00 по времени зрителя, и не раньше.

    Раньше ночные получатели сразу уходили обратно в очередь сборки: сборщик
    забирал их снова, ходил за контактами, видел ночь и возвращал — по кругу
    до утра.
    """
    from datetime import timedelta

    from services.relay import OutboxRelay
    from tests.unit.fakes import FakeOutbox

    db.template_versions[('new_episode', 1)] = template('new_episode')
    viewer = uuid4()
    directory.recipients = {viewer: recipient(viewer, timezone='Europe/Moscow')}
    await assembly.assemble(render_message([viewer]), now=NIGHT_MSK)
    calls_after_assembly = directory.calls
    publisher = FakePublisher()
    relay = OutboxRelay(FakeOutbox(db), publisher, 100, timedelta(seconds=30), timedelta(seconds=60))
    morning = datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)  # 09:00 в Москве

    assert await relay.relay_once(morning - timedelta(minutes=1)) == 0
    assert await relay.relay_once(morning) == 1
    assert publisher.of(STAGE_RENDER)[0]['user_ids'] == [str(viewer)]
    # Пока письмо ждало, за контактами никто не ходил.
    assert directory.calls == calls_after_assembly


async def test_viewers_with_the_same_morning_share_one_task(
    assembly: AssemblyService, db: Database, directory: FakeContactDirectory,
) -> None:
    """Зрители одного часового пояса откладываются одним заданием, разных — разными."""
    db.template_versions[('new_episode', 1)] = template('new_episode')
    first, second, far = uuid4(), uuid4(), uuid4()
    directory.recipients = {
        first: recipient(first, timezone='Europe/Moscow'),
        second: recipient(second, timezone='Europe/Moscow'),
        far: recipient(far, timezone='Asia/Vladivostok'),
    }
    # 23:00 в Москве — 06:00 во Владивостоке: ночь у всех, а утро у москвичей
    # и у дальневосточника наступает в разные моменты.
    moment = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)

    await assembly.assemble(render_message([first, second, far]), now=moment)

    tasks = sorted(sorted(item['user_ids']) for item in db.outbox_of(STAGE_RENDER))
    assert tasks == sorted([sorted([str(first), str(second)]), [str(far)]])
