"""Шаблоны, подписки, уведомления и отправки."""

from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from models.enums import Channel, DeliveryStatus

# Дженерик через TypeVar, а не синтаксисом PEP 695: `class Page[T]` требует
# Python 3.12, а CI по заданию гоняет код и на 3.10.
T = TypeVar('T')


class Template(BaseModel):
    """Шаблон письма.

    Версия растёт при каждой правке. В сообщение очереди едет пара
    (код, версия), а не текст: 30 КБ вёрстки на миллион адресатов — это
    30 ГБ в брокере (ADR-12). Версия нужна, чтобы правка посреди рассылки не
    разослала половине зрителей одно письмо, а половине другое.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    code: str
    name: str
    channel: Channel
    subject: str
    body: str
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class Subscription(BaseModel):
    """Согласие зрителя получать уведомления определённого типа по каналу.

    Отсутствие записи означает согласие: подписывать заново всех при
    добавлении нового типа уведомлений никто не станет. Выключение — это
    явная запись с `enabled=False`.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: UUID
    template_code: str
    channel: Channel
    enabled: bool
    updated_at: datetime


class NotificationRecord(BaseModel):
    """Уведомление о данных: о чём зрителю уже сообщали.

    `content_version` — версия данных, о которой отправлено последнее письмо.
    Это и есть защита от повторов: вышла восьмая серия при записанной
    восьмой — письма не будет (ФТ-6).
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    user_id: UUID
    template_code: str
    content_id: str
    content_version: int | None
    last_sent_at: datetime | None


class Delivery(BaseModel):
    """Факт отправки одного сообщения одному зрителю."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    idempotency_key: str
    user_id: UUID
    channel: Channel
    template_code: str
    subject: str
    status: DeliveryStatus
    error: str | None
    created_at: datetime
    sent_at: datetime | None


class Recipient(BaseModel):
    """Получатель, как его видит сборщик письма.

    Контакты приходят из справочника сервиса авторизации, настройки — из
    своей базы. Здесь они уже сведены вместе.
    """

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    email: str | None
    first_name: str | None
    last_name: str | None
    timezone: str | None

    @property
    def full_name(self) -> str:
        """Имя для обращения; пустая строка, если имени нет."""
        return ' '.join(part for part in (self.first_name, self.last_name) if part)


class RenderedMessage(BaseModel):
    """Готовое сообщение — всё, что нужно отправителю."""

    model_config = ConfigDict(frozen=True)

    idempotency_key: str
    user_id: UUID
    channel: Channel
    template_code: str
    address: str
    subject: str
    body: str


class Page(BaseModel, Generic[T]):
    """Страница списка."""

    model_config = ConfigDict(frozen=True)

    items: list[T]
    total: int = Field(ge=0)
    page_number: int = Field(ge=1)
    page_size: int = Field(ge=1)


class ShortLink(BaseModel):
    """Короткая ссылка из письма."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    key: str
    target_url: str
    user_id: UUID | None
    expires_at: datetime | None
    visits: int
    # Служебная пометка: по ней переход подтверждает адрес почты.
    purpose: str | None
    created_at: datetime


class TemplateDraft(BaseModel):
    """Что менеджер присылает при создании или правке шаблона."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1, max_length=64, pattern=r'^[a-z][a-z0-9_]*$')
    name: str = Field(min_length=1, max_length=128)
    channel: Channel = Channel.EMAIL
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)
    is_active: bool = True


class DeferredContext(BaseModel):
    """Накопленное откладываемое уведомление.

    Хранит, сколько событий пришло с прошлой отправки: письмо о лайках уходит
    не на каждый лайк, а одно — «ваш комментарий оценили 12 раз».
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(ge=1)
    context: dict[str, Any] = Field(default_factory=dict)
