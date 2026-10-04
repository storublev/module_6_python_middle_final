"""Рассылки менеджера и генератор автоматических событий.

Три способа запуска из чек-листа задания:

* **сразу** — рассылка создаётся в состоянии «идёт», и событие ставится в
  очередь тем же вызовом (через outbox, см. `services/ingest.py`);
* **отложенно** (через 1–n часов) — `scheduled_at`, запуск подхватит генератор;
* **повторяемо** («каждую пятницу», «каждый Новый год») — расписание в формате
  cron.

Защита от дублей после простоя (НФТ-5) — ключ запуска `(рассылка, период)` с
уникальным индексом. Генератор, проснувшийся через сутки, увидит, что запуск
за прошлую пятницу уже был, и не разошлёт её второй раз. Период для
повторяемой рассылки — момент её срабатывания, для разовой — сама рассылка.

Расписание разбирается своим кодом, а не библиотекой: нужны пять стандартных
полей cron со списками, диапазонами и шагами, и тянуть ради этого зависимость
с собственным диалектом незачем.
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from core.request_id import get_request_id
from models.campaign import Campaign, CampaignDraft
from models.enums import CampaignStatus, Urgency
from models.outbox import OutboxDraft
from services.errors import CampaignNotFoundError, CampaignNotRunnableError, TemplateNotFoundError
from services.messages import PlanMessage
from storage.base import CampaignRepository, TemplateRepository
from storage.rabbit import STAGE_PLAN

logger = logging.getLogger(__name__)

# Сколько минут назад ещё считается «пора»: генератор просыпается не ровно в
# срок, и рассылку, чей момент прошёл минуту назад, отправить нужно.
CRON_TOLERANCE = timedelta(minutes=5)


class CampaignService:
    """Создание, запуск и остановка рассылок."""

    def __init__(
        self,
        campaigns: CampaignRepository,
        templates: TemplateRepository,
    ) -> None:
        self._campaigns = campaigns
        self._templates = templates

    async def create(self, draft: CampaignDraft, created_by: str | None) -> Campaign:
        """Создаёт рассылку и, если она без расписания, сразу запускает.

        Raises:
            TemplateNotFoundError: такого шаблона нет.
        """
        if await self._templates.get(draft.template_code) is None:
            raise TemplateNotFoundError
        campaign = await self._campaigns.create(draft, created_by)
        if campaign.scheduled_at is None and campaign.cron is None:
            await self.launch(campaign, period_key='once')
        logger.info(
            'Рассылка создана', extra={'campaign_id': str(campaign.id), 'status': campaign.status.value},
        )
        return campaign

    async def list_all(self) -> list[Campaign]:
        return await self._campaigns.list_all()

    async def get(self, campaign_id: UUID) -> Campaign:
        """Отдаёт рассылку.

        Raises:
            CampaignNotFoundError: такой рассылки нет.
        """
        campaign = await self._campaigns.get(campaign_id)
        if campaign is None:
            raise CampaignNotFoundError
        return campaign

    async def cancel(self, campaign_id: UUID) -> Campaign:
        """Отменяет рассылку.

        Raises:
            CampaignNotFoundError: такой рассылки нет.
        """
        campaign = await self._campaigns.set_status(campaign_id, CampaignStatus.CANCELLED.value)
        if campaign is None:
            raise CampaignNotFoundError
        return campaign

    async def run_now(self, campaign_id: UUID) -> Campaign:
        """Запускает рассылку немедленно — кнопка «Запустить» в админ-панели.

        Raises:
            CampaignNotFoundError: такой рассылки нет.
            CampaignNotRunnableError: рассылка отменена или уже завершена.
        """
        campaign = await self._campaigns.get(campaign_id)
        if campaign is None:
            raise CampaignNotFoundError
        if campaign.status in (CampaignStatus.CANCELLED, CampaignStatus.DONE):
            raise CampaignNotRunnableError
        await self.launch(campaign, period_key=f'manual:{datetime.now(timezone.utc).isoformat()}')
        return campaign

    async def launch(self, campaign: Campaign, period_key: str) -> bool:
        """Ставит событие рассылки в очередь. False — этот период уже запускали.

        Ключ периода и есть защита от повторов: два генератора, проснувшиеся
        одновременно, не разошлют одно и то же дважды. Отметка запуска и
        задание на публикацию пишутся вместе: сбой брокера больше не оставляет
        запуск «состоявшимся» без единого письма.
        """
        event_id = uuid4()
        context = await self._campaigns.context_of(campaign.id)
        message = PlanMessage(
            event_id=event_id,
            routing_key='campaign-reporting.v1.started',
            template_code=campaign.template_code,
            channel=campaign.channel,
            urgency=Urgency.INSTANT,
            audience=campaign.audience,
            context=context,
            campaign_id=campaign.id,
        )
        publication = OutboxDraft(
            stage=STAGE_PLAN, payload=message.model_dump(mode='json'), request_id=get_request_id(),
        )
        # Разовая рассылка после запуска завершена — больше её запускать не нужно.
        finish = campaign.cron is None
        if not await self._campaigns.claim_run(campaign.id, period_key, event_id, publication, finish):
            logger.info(
                'Запуск рассылки за этот период уже был, повтор пропущен',
                extra={'campaign_id': str(campaign.id), 'period': period_key},
            )
            return False
        logger.info(
            'Рассылка запущена', extra={'campaign_id': str(campaign.id), 'period': period_key},
        )
        return True

    async def launch_due(self, moment: datetime) -> int:
        """Запускает все рассылки, которым пора. Возвращает число запущенных.

        Это и есть генератор автоматических событий: он просыпается по
        расписанию, смотрит, чей срок настал, и публикует события.
        """
        launched = 0
        for campaign in await self._campaigns.due(moment):
            period_key = self._period_key(campaign, moment)
            if period_key is None:
                continue
            if await self.launch(campaign, period_key):
                launched += 1
        return launched

    @staticmethod
    def _period_key(campaign: Campaign, moment: datetime) -> str | None:
        """Ключ периода запуска или None, если сейчас не пора."""
        if campaign.cron:
            fired = last_fire_time(campaign.cron, moment)
            if fired is None or moment - fired > CRON_TOLERANCE:
                return None
            return fired.isoformat()
        if campaign.scheduled_at is not None and campaign.scheduled_at <= moment:
            return campaign.scheduled_at.isoformat()
        return None


def last_fire_time(expression: str, moment: datetime) -> datetime | None:
    """Ближайший к `moment` момент срабатывания расписания, не позже него.

    Ищем назад по минутам в пределах суток: этого хватает любому расписанию,
    у которого есть хотя бы одно срабатывание в сутки, а у остальных
    («каждый Новый год») срабатывание всё равно попадёт в окно допуска.
    """
    fields = _parse(expression)
    if fields is None:
        logger.error('Расписание не разбирается: %s', expression)
        return None
    minutes, hours, days, months, weekdays = fields
    probe = moment.replace(second=0, microsecond=0)
    for _ in range(60 * 24 + 1):
        if (
            probe.minute in minutes
            and probe.hour in hours
            and probe.day in days
            and probe.month in months
            # В cron воскресенье — 0, в Python — 6.
            and (probe.weekday() + 1) % 7 in weekdays
        ):
            return probe
        probe -= timedelta(minutes=1)
    return None


def _parse(expression: str) -> tuple[set[int], ...] | None:
    """Разбирает пять полей cron в наборы допустимых значений."""
    parts = expression.split()
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
    if len(parts) != len(ranges):
        return None
    result = []
    for part, (low, high) in zip(parts, ranges, strict=False):
        values = _field(part, low, high)
        if values is None:
            return None
        result.append(values)
    return tuple(result)


def _field(part: str, low: int, high: int) -> set[int] | None:
    values: set[int] = set()
    for piece in part.split(','):
        body, _, step_text = piece.partition('/')
        step = int(step_text) if step_text.isdigit() else 1
        if step <= 0:
            return None
        if body in ('*', ''):
            start, end = low, high
        elif '-' in body:
            start_text, _, end_text = body.partition('-')
            if not (start_text.isdigit() and end_text.isdigit()):
                return None
            start, end = int(start_text), int(end_text)
        elif body.isdigit():
            start = end = int(body)
        else:
            return None
        if start < low or end > high or start > end:
            return None
        values.update(range(start, end + 1, step))
    return values or None
