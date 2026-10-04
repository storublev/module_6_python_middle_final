"""Событие — то, из чего рождается уведомление.

Событие описывает **факт**, а не письмо: «вышла восьмая серия сериала N»,
а не «отправить Пете письмо с таким-то текстом». Кому и что писать, решают
дальше по конвейеру — так событие остаётся контрактом, который не меняется
при правке шаблона или списка каналов.

Ключевое правило гибридной схемы (docs/architecture/notifications.md): в
событии едут **идентификаторы и версия**, а не содержимое. Адрес и имя
получателя воркер добирает сам, тяжёлую статистику считает генератор заранее
и кладёт в витрину, а сюда попадает только ссылка на неё.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.enums import AudienceKind, Channel, Urgency

# Ключ маршрутизации по правилу урока: [сущность]-reporting.[версия].[событие].
ROUTING_KEY_PATTERN = r'^[a-z][a-z0-9-]*-reporting\.v\d+\.[a-z][a-z0-9-]*$'
MAX_USERS_PER_EVENT = 1000


class Audience(BaseModel):
    """Кому адресовано событие."""

    model_config = ConfigDict(frozen=True)

    kind: AudienceKind
    # Для kind=users: кому именно. Предел тот же, что у справочника контактов
    # сервиса авторизации, — большие группы описываются сегментом.
    user_ids: list[UUID] = Field(default_factory=list, max_length=MAX_USERS_PER_EVENT)
    # Для kind=segment: правило отбора. Разворачивает его планировщик, а не
    # отправитель события: только он знает, как устроены подписки.
    segment: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def check_shape(self) -> 'Audience':
        if self.kind is AudienceKind.USERS and not self.user_ids:
            raise ValueError('audience.user_ids must not be empty for kind=users')
        if self.kind is AudienceKind.SEGMENT and not self.segment:
            raise ValueError('audience.segment must not be empty for kind=segment')
        return self


class Event(BaseModel):
    """Принятое событие.

    `event_id` задаёт отправитель. Это ключ идемпотентности: повтор запроса
    после потерянного ответа не должен создавать второе уведомление (ФТ-2).
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID
    routing_key: str = Field(pattern=ROUTING_KEY_PATTERN)
    template_code: str = Field(min_length=1, max_length=64)
    channel: Channel = Channel.EMAIL
    urgency: Urgency = Urgency.INSTANT
    audience: Audience
    # Идентификатор данных, с изменением которых связано уведомление
    # (например, сериала). По нему уведомление находится и по нему же
    # сверяется версия.
    content_id: str | None = Field(default=None, max_length=128)
    # Версия данных, о которой сообщаем. Совпадение с уже отправленной гасит
    # событие: о восьмой серии нельзя писать дважды (ФТ-6).
    content_version: int | None = None
    # Что подставить в шаблон сверх данных получателя: имя сериала, номер
    # серии. Персональной статистики здесь нет — она в витрине.
    context: dict[str, Any] = Field(default_factory=dict)
    # Ключ витрины с персональными данными на эту рассылку, если она есть.
    dataset_key: str | None = Field(default=None, max_length=128)
    occurred_at: datetime | None = None


class AcceptedEvent(BaseModel):
    """Ответ на приём события: что именно приняли и не повтор ли это."""

    model_config = ConfigDict(frozen=True)

    event_id: UUID
    # False означает, что такое событие уже принимали: письмо не задвоится.
    accepted: bool
