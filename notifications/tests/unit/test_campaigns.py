"""Рассылки менеджера и генератор автоматических событий."""

from datetime import datetime, timedelta, timezone

import pytest

from models.campaign import CampaignDraft
from models.enums import AudienceKind, CampaignStatus
from models.event import Audience
from services.campaigns import CampaignService, last_fire_time
from services.errors import CampaignNotRunnableError, TemplateNotFoundError
from storage.rabbit import STAGE_PLAN
from tests.unit.fakes import Database, template

FRIDAY_18_00 = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)
AUDIENCE = Audience(kind=AudienceKind.ALL)


def draft(**overrides: object) -> CampaignDraft:
    data: dict = {'title': 'Подборка недели', 'template_code': 'weekly_digest', 'audience': AUDIENCE}
    data.update(overrides)
    return CampaignDraft(**data)


async def test_campaign_without_schedule_starts_immediately(
    campaigns: CampaignService, db: Database,
) -> None:
    """Рассылка без расписания уходит сразу — это кнопка «Отправить»."""
    db.templates['weekly_digest'] = template('weekly_digest')

    campaign = await campaigns.create(draft(), created_by='manager')

    assert campaign.status is CampaignStatus.RUNNING
    assert len(db.outbox_of(STAGE_PLAN)) == 1


async def test_scheduled_campaign_waits(
    campaigns: CampaignService, db: Database,
) -> None:
    """Отложенная рассылка не уходит в момент создания."""
    db.templates['weekly_digest'] = template('weekly_digest')
    later = datetime.now(timezone.utc) + timedelta(hours=3)

    campaign = await campaigns.create(draft(scheduled_at=later), created_by='manager')

    assert campaign.status is CampaignStatus.SCHEDULED
    assert db.outbox_of(STAGE_PLAN) == []


async def test_campaign_with_unknown_template_is_rejected(campaigns: CampaignService) -> None:
    """Рассылку по несуществующему шаблону создать нельзя.

    Иначе она дошла бы до планировщика и там тихо умерла, а менеджер бы думал,
    что письма ушли.
    """
    with pytest.raises(TemplateNotFoundError):
        await campaigns.create(draft(), created_by='manager')


async def test_due_scheduled_campaign_is_launched(
    campaigns: CampaignService, db: Database,
) -> None:
    """Генератор запускает отложенную рассылку, когда её время пришло."""
    db.templates['weekly_digest'] = template('weekly_digest')
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await campaigns.create(draft(scheduled_at=past), created_by='manager')

    launched = await campaigns.launch_due(datetime.now(timezone.utc))

    assert launched == 1
    assert len(db.outbox_of(STAGE_PLAN)) == 1


async def test_second_pass_does_not_launch_the_same_period_twice(
    campaigns: CampaignService, db: Database,
) -> None:
    """Два прохода генератора подряд не рассылают одно и то же дважды.

    Это НФТ-5: ключ запуска `(рассылка, период)` уникален, поэтому ни повтор
    прохода, ни второй экземпляр генератора не создают дубля.
    """
    db.templates['weekly_digest'] = template('weekly_digest')
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await campaigns.create(draft(scheduled_at=past), created_by='manager')

    first = await campaigns.launch_due(datetime.now(timezone.utc))
    second = await campaigns.launch_due(datetime.now(timezone.utc))

    assert (first, second) == (1, 0)
    assert len(db.outbox_of(STAGE_PLAN)) == 1


async def test_recurring_campaign_launches_once_per_period(
    campaigns: CampaignService, db: Database,
) -> None:
    """Повторяемая рассылка уходит один раз за срабатывание расписания.

    Генератор просыпается каждую минуту, а рассылка «каждую пятницу в 18:00»
    должна уйти ровно один раз, а не шестьдесят.
    """
    db.templates['weekly_digest'] = template('weekly_digest')
    await campaigns.create(draft(cron='0 18 * * 5'), created_by='manager')

    at_18_00 = await campaigns.launch_due(FRIDAY_18_00)
    at_18_01 = await campaigns.launch_due(FRIDAY_18_00 + timedelta(minutes=1))

    assert (at_18_00, at_18_01) == (1, 0)


async def test_recurring_campaign_launches_again_next_week(
    campaigns: CampaignService, db: Database,
) -> None:
    """Через неделю та же рассылка уходит снова: это другой период."""
    db.templates['weekly_digest'] = template('weekly_digest')
    await campaigns.create(draft(cron='0 18 * * 5'), created_by='manager')

    await campaigns.launch_due(FRIDAY_18_00)
    next_week = await campaigns.launch_due(FRIDAY_18_00 + timedelta(days=7))

    assert next_week == 1
    assert len(db.outbox_of(STAGE_PLAN)) == 2


async def test_downtime_does_not_resend_old_periods(
    campaigns: CampaignService, db: Database,
) -> None:
    """Генератор, проснувшийся через сутки, не рассылает прошлые периоды.

    Проверяется прямо требование задания: «в случае простоя генератора после
    его запуска не должны дублироваться старые и новые события».
    """
    db.templates['weekly_digest'] = template('weekly_digest')
    await campaigns.create(draft(cron='0 18 * * 5'), created_by='manager')

    # Генератор лежал сутки и проснулся в субботу.
    launched = await campaigns.launch_due(FRIDAY_18_00 + timedelta(days=1))

    assert launched == 0
    assert db.outbox_of(STAGE_PLAN) == []


async def test_cancelled_campaign_cannot_be_run(
    campaigns: CampaignService, db: Database,
) -> None:
    """Отменённую рассылку запустить нельзя."""
    db.templates['weekly_digest'] = template('weekly_digest')
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    campaign = await campaigns.create(draft(scheduled_at=later), created_by='manager')
    await campaigns.cancel(campaign.id)

    with pytest.raises(CampaignNotRunnableError):
        await campaigns.run_now(campaign.id)


async def test_campaign_context_reaches_the_queue(
    campaigns: CampaignService, db: Database,
) -> None:
    """Данные, заданные менеджером, доезжают до планировщика."""
    db.templates['weekly_digest'] = template('weekly_digest')

    await campaigns.create(draft(context={'items': ['Матрица']}), created_by='manager')

    assert db.outbox_of(STAGE_PLAN)[0]['context'] == {'items': ['Матрица']}


def test_cron_matches_weekday_and_time() -> None:
    """Расписание «каждую пятницу в 18:00» срабатывает в пятницу в 18:00."""
    assert last_fire_time('0 18 * * 5', FRIDAY_18_00) == FRIDAY_18_00


def test_cron_returns_previous_fire_time() -> None:
    """Через минуту после срабатывания расписание помнит именно его момент."""
    assert last_fire_time('0 18 * * 5', FRIDAY_18_00 + timedelta(minutes=1)) == FRIDAY_18_00


def test_cron_supports_steps_and_ranges() -> None:
    """Списки, диапазоны и шаги в полях разбираются."""
    moment = datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc)

    assert last_fire_time('*/15 9-18 * * 1-5', moment) == moment


def test_broken_cron_never_fires() -> None:
    """Неразбираемое расписание не срабатывает, а не срабатывает каждую минуту."""
    assert last_fire_time('каждую пятницу', FRIDAY_18_00) is None
    assert last_fire_time('0 18 * *', FRIDAY_18_00) is None
    assert last_fire_time('99 18 * * 5', FRIDAY_18_00) is None


async def test_campaign_launch_survives_broker_outage(campaigns: CampaignService, db: Database) -> None:
    """Запуск рассылки при лежащем брокере не «съедается»: событие ждёт в outbox.

    Раньше запуск отмечался до публикации: брокер не принял событие, а
    повторить запуск было уже нельзя — период считался отработанным.
    """
    from services.relay import OutboxRelay
    from storage.base import StorageUnavailableError
    from tests.unit.fakes import FakeOutbox, FakePublisher

    db.templates['weekly_digest'] = template('weekly_digest')
    publisher = FakePublisher()
    publisher.fail_with = StorageUnavailableError('брокер лежит')
    relay = OutboxRelay(FakeOutbox(db), publisher, 100, timedelta(seconds=30), timedelta(seconds=60))

    await campaigns.create(draft(), created_by='manager')
    now = datetime.now(timezone.utc)
    await relay.relay_once(now)
    publisher.fail_with = None
    await relay.relay_once(now + timedelta(minutes=1))

    assert len(publisher.of(STAGE_PLAN)) == 1
