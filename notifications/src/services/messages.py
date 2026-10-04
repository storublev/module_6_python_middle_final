"""Контракты сообщений между этапами конвейера.

Этапы общаются через очередь, а значит — через границу процесса. Границу
описывают явными моделями, а не словарями: иначе опечатка в ключе вскроется
не тестом, а неотправленным письмом.

Что важно в этих контрактах: **в них едут идентификаторы и версии, а не
содержимое**. Адрес и имя получателя добирает сборщик, тяжёлую статистику
считает генератор и кладёт в витрину, текст письма берётся по коду и версии
шаблона. Это и есть гибридная схема из задания 1.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from models.enums import Channel, Urgency
from models.event import Audience


class PlanMessage(BaseModel):
    """Что получает планировщик: событие как оно пришло."""

    model_config = ConfigDict(frozen=True)

    event_id: UUID
    routing_key: str
    template_code: str
    channel: Channel
    urgency: Urgency
    audience: Audience
    content_id: str | None = None
    content_version: int | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    dataset_key: str | None = None
    campaign_id: UUID | None = None


class RenderMessage(BaseModel):
    """Что получает сборщик: пачка получателей и чем их наполнить.

    `user_ids` — не больше размера пачки из настроек: планировщик режет
    сегмент на части сам, чтобы одно сообщение не выросло до мегабайтов.
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID
    template_code: str
    template_version: int
    channel: Channel
    user_ids: list[UUID] = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    content_id: str | None = None
    content_version: int | None = None
    dataset_key: str | None = None


class SendMessage(BaseModel):
    """Что получает отправитель: готовое письмо и чем отметить отправку."""

    model_config = ConfigDict(frozen=True)

    idempotency_key: str
    user_id: UUID
    channel: Channel
    template_code: str
    address: str
    subject: str
    body: str
    content_id: str | None = None
    content_version: int | None = None
    # Часовой пояс получателя на момент сборки: по нему отправитель ещё раз
    # проверяет тихие часы. None — пояс не задан, берётся пояс по умолчанию.
    # Необязательный, чтобы сообщения, собранные до появления поля, не
    # застряли в очереди на разборе.
    timezone: str | None = None
