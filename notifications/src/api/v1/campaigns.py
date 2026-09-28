"""Рассылки менеджера: создать, запустить, отменить.

Три способа запуска из чек-листа задания: сразу, отложенно (`scheduled_at`) и
повторяемо (`cron`). Текст письма здесь не передаётся — только код шаблона:
шаблон общий с автоматическими уведомлениями (ADR-12).
"""

from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter

from api.dependencies import CampaignServiceDep, ServiceToken
from api.errors import error_responses
from api.v1.schemas import CampaignDraftSchema, CampaignSchema
from models.campaign import CampaignDraft
from services.errors import (
    CampaignNotFoundError,
    CampaignNotRunnableError,
    ServiceTokenInvalidError,
    TemplateNotFoundError,
)

router = APIRouter()


@router.get(
    '',
    response_model=list[CampaignSchema],
    summary='Все рассылки',
    responses=error_responses(ServiceTokenInvalidError),
)
async def list_campaigns(campaigns: CampaignServiceDep, _: ServiceToken = None) -> list[CampaignSchema]:
    return [CampaignSchema.model_validate(item) for item in await campaigns.list_all()]


@router.post(
    '',
    response_model=CampaignSchema,
    status_code=HTTPStatus.CREATED,
    summary='Создать рассылку',
    description=(
        'Без `scheduled_at` и `cron` рассылка уходит сразу. С `scheduled_at` — в указанное время. '
        'С `cron` — по расписанию из пяти полей, например `0 18 * * 5` (каждую пятницу в 18:00).\n\n'
        'Повторный запуск за тот же период не рассылает письма дважды: у каждого запуска есть ключ периода '
        'с уникальным индексом, поэтому простой генератора не превращается в дубли.'
    ),
    responses=error_responses(ServiceTokenInvalidError, TemplateNotFoundError),
)
async def create_campaign(
    body: CampaignDraftSchema, campaigns: CampaignServiceDep, _: ServiceToken = None,
) -> CampaignSchema:
    created = await campaigns.create(CampaignDraft.model_validate(body.model_dump()), created_by=None)
    return CampaignSchema.model_validate(created)


@router.post(
    '/{campaign_id}/run',
    response_model=CampaignSchema,
    summary='Запустить рассылку сейчас',
    responses=error_responses(ServiceTokenInvalidError, CampaignNotFoundError, CampaignNotRunnableError),
)
async def run_campaign(
    campaign_id: UUID, campaigns: CampaignServiceDep, _: ServiceToken = None,
) -> CampaignSchema:
    return CampaignSchema.model_validate(await campaigns.run_now(campaign_id))


@router.post(
    '/{campaign_id}/cancel',
    response_model=CampaignSchema,
    summary='Отменить рассылку',
    responses=error_responses(ServiceTokenInvalidError, CampaignNotFoundError),
)
async def cancel_campaign(
    campaign_id: UUID, campaigns: CampaignServiceDep, _: ServiceToken = None,
) -> CampaignSchema:
    return CampaignSchema.model_validate(await campaigns.cancel(campaign_id))
