"""Рассылки менеджера: разовые, отложенные и повторяющиеся."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.enums import CampaignStatus, Channel
from models.event import Audience


class Campaign(BaseModel):
    """Рассылка, созданная менеджером в админ-панели.

    Три способа запуска закрывают чек-лист задания: сразу, через n часов
    (`scheduled_at`) и повторяемо (`cron`, например «каждую пятницу в 18:00»).
    Текст письма здесь не хранится — только `template_code`: шаблон общий с
    автоматическими уведомлениями, чего требует задание.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    title: str
    template_code: str
    channel: Channel
    audience: Audience
    status: CampaignStatus
    # Когда запустить разовую рассылку. None — запустить сразу.
    scheduled_at: datetime | None
    # Расписание повторяемой рассылки в формате cron (минуты, часы, день
    # месяца, месяц, день недели). None — рассылка разовая.
    cron: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class CampaignDraft(BaseModel):
    """Что присылают при создании рассылки."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=255)
    template_code: str = Field(min_length=1, max_length=64)
    channel: Channel = Channel.EMAIL
    audience: Audience
    scheduled_at: datetime | None = None
    cron: str | None = Field(default=None, max_length=128)
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def check_schedule(self) -> 'CampaignDraft':
        if self.scheduled_at and self.cron:
            raise ValueError('scheduled_at and cron are mutually exclusive')
        return self


class CampaignRun(BaseModel):
    """Запуск рассылки за конкретный период.

    Ключ `(campaign_id, period_key)` уникален — это и есть защита от повторов
    после простоя генератора (НФТ-5): проснувшись через сутки, он увидит, что
    запуск за прошлую пятницу уже был, и не разошлёт её второй раз.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    campaign_id: UUID
    period_key: str
    event_id: UUID
    started_at: datetime
