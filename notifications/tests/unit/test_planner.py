"""Планировщик: кому писать, кому не писать и как режется пачка."""

from uuid import uuid4

from models.enums import AudienceKind, Channel
from models.event import Audience
from services.messages import PlanMessage
from services.planner import PlannerService
from storage.rabbit import STAGE_RENDER
from tests.unit.conftest import BATCH_SIZE
from tests.unit.fakes import (
    Database,
    FakeContactDirectory,
    FakePublisher,
    FakeSubscriptionRepository,
    FakeTemplateRepository,
    recipient,
    template,
)


def plan_message(user_ids: list, **overrides: object) -> PlanMessage:
    data: dict = {
        'event_id': uuid4(),
        'routing_key': 'film-reporting.v1.episode-added',
        'template_code': 'new_episode',
        'channel': Channel.EMAIL,
        'urgency': 'instant',
    }
    if user_ids:
        data['audience'] = Audience(kind=AudienceKind.USERS, user_ids=user_ids)
    data.update(overrides)
    return PlanMessage(**data)


async def test_missing_template_stops_event(planner: PlannerService, publisher: FakePublisher) -> None:
    """Событие без шаблона не разворачивается: собирать письмо не из чего."""
    planned = await planner.plan(plan_message([uuid4()]))

    assert planned == 0
    assert publisher.of(STAGE_RENDER) == []


async def test_inactive_template_stops_event(
    planner: PlannerService, db: Database, publisher: FakePublisher,
) -> None:
    """Выключенный шаблон не рассылается: его выключили именно для этого."""
    db.templates['new_episode'] = template('new_episode', is_active=False)

    planned = await planner.plan(plan_message([uuid4()]))

    assert planned == 0
    assert publisher.of(STAGE_RENDER) == []


async def test_recipients_are_split_into_batches(
    planner: PlannerService, db: Database, publisher: FakePublisher,
) -> None:
    """Получатели режутся на пачки заданного размера.

    Пачка — единица работы сборщика: на неё приходится один запрос в
    справочник контактов, поэтому её размер ограничен.
    """
    db.templates['new_episode'] = template('new_episode')
    users = [uuid4() for _ in range(BATCH_SIZE * 2 + 1)]

    planned = await planner.plan(plan_message(users))

    assert planned == len(users)
    batches = publisher.of(STAGE_RENDER)
    assert [len(batch['user_ids']) for batch in batches] == [BATCH_SIZE, BATCH_SIZE, 1]


async def test_unsubscribed_viewer_is_dropped(
    planner: PlannerService, db: Database, subscriptions_repo: FakeSubscriptionRepository,
    publisher: FakePublisher,
) -> None:
    """Отписавшемуся письмо не собирается."""
    db.templates['new_episode'] = template('new_episode')
    quiet, loud = uuid4(), uuid4()
    await subscriptions_repo.set_enabled(quiet, 'new_episode', Channel.EMAIL, enabled=False)

    planned = await planner.plan(plan_message([quiet, loud]))

    assert planned == 1
    assert publisher.of(STAGE_RENDER)[0]['user_ids'] == [str(loud)]


async def test_absent_subscription_means_consent(
    planner: PlannerService, db: Database, publisher: FakePublisher,
) -> None:
    """Отсутствие записи о подписке означает согласие.

    Иначе при добавлении нового типа уведомлений пришлось бы завести согласие
    миллиону зрителей, а до тех пор не писать никому.
    """
    db.templates['new_episode'] = template('new_episode')

    planned = await planner.plan(plan_message([uuid4()]))

    assert planned == 1


async def test_same_content_version_is_not_repeated(
    planner: PlannerService, db: Database, notifications_repo, publisher: FakePublisher,
) -> None:
    """О той же версии данных второй раз не пишем (ФТ-6).

    Восьмая серия вышла один раз — писем о ней тоже должно быть одно.
    """
    db.templates['new_episode'] = template('new_episode')
    viewer = uuid4()
    from datetime import datetime, timezone

    await notifications_repo.mark_notified(
        viewer, 'new_episode', 'series-42', 8, datetime.now(timezone.utc),
    )

    planned = await planner.plan(
        plan_message([viewer], content_id='series-42', content_version=8),
    )

    assert planned == 0


async def test_newer_content_version_is_sent(
    planner: PlannerService, db: Database, notifications_repo,
) -> None:
    """О девятой серии пишем, хотя о восьмой уже писали."""
    db.templates['new_episode'] = template('new_episode')
    viewer = uuid4()
    from datetime import datetime, timezone

    await notifications_repo.mark_notified(
        viewer, 'new_episode', 'series-42', 8, datetime.now(timezone.utc),
    )

    planned = await planner.plan(
        plan_message([viewer], content_id='series-42', content_version=9),
    )

    assert planned == 1


async def test_audience_all_walks_directory_by_pages(
    planner: PlannerService, db: Database, directory: FakeContactDirectory, publisher: FakePublisher,
) -> None:
    """Рассылка всем обходит справочник постранично, а не тянет всех в память.

    Миллион адресатов в одном сообщении не поместится, да и держать их в
    памяти планировщика негде.
    """
    db.templates['weekly_digest'] = template('weekly_digest')
    directory.recipients = {
        (user_id := uuid4()): recipient(user_id) for _ in range(BATCH_SIZE * 2 + 1)
    }

    planned = await planner.plan(
        plan_message([], template_code='weekly_digest', audience=Audience(kind=AudienceKind.ALL)),
    )

    assert planned == len(directory.recipients)
    # Обход идёт страницами размера пачки, значит справочник спрошен больше
    # одного раза, но не по разу на зрителя.
    assert 1 < directory.calls <= len(directory.recipients)


async def test_template_version_is_fixed_at_planning(
    planner: PlannerService, db: Database, templates_repo: FakeTemplateRepository, publisher: FakePublisher,
) -> None:
    """Версия шаблона фиксируется при планировании.

    Правка шаблона посреди рассылки не должна разослать половине зрителей одно
    письмо, а половине другое.
    """
    db.templates['new_episode'] = template('new_episode', version=3)

    await planner.plan(plan_message([uuid4()]))

    assert publisher.of(STAGE_RENDER)[0]['template_version'] == 3
