"""Схемы запросов и ответов. Описания и примеры полей попадают в OpenAPI."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from models.enums import CampaignStatus, Channel, DeliveryStatus, Urgency
from models.event import ROUTING_KEY_PATTERN, Audience


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EventSchema(BaseModel):
    """Событие, из которого рождается уведомление."""

    event_id: UUID = Field(
        description='Идентификатор события, который задаёт отправитель. '
                    'Повтор запроса с тем же значением не создаёт второго уведомления',
        examples=['0f0e7c9c-6b28-4f0e-9f6a-6a1a4b1f2b3c'],
    )
    routing_key: str = Field(
        pattern=ROUTING_KEY_PATTERN,
        description='Ключ маршрутизации вида `[сущность]-reporting.[версия].[событие]`. '
                    'Имя получателя в нём не указывается: событие описывает факт',
        examples=['film-reporting.v1.episode-added'],
    )
    template_code: str = Field(min_length=1, max_length=64, description='Код шаблона письма', examples=['new_episode'])
    channel: Channel = Field(default=Channel.EMAIL, description='Канал доставки')
    urgency: Urgency = Field(default=Urgency.INSTANT, description='Мгновенное уведомление или откладываемое')
    audience: Audience = Field(description='Кому адресовано: список, сегмент или все зрители')
    content_id: str | None = Field(
        default=None, max_length=128,
        description='Идентификатор данных, с изменением которых связано уведомление', examples=['series-42'],
    )
    content_version: int | None = Field(
        default=None,
        description='Версия данных. О той же версии второй раз не пишем', examples=[8],
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description='Что подставить в шаблон: название фильма, номер серии. '
                    'Персональной статистики здесь нет — она в витрине',
        examples=[{'film_title': 'Матрица', 'episode': 8}],
    )
    dataset_key: str | None = Field(
        default=None, max_length=128, description='Ключ витрины с персональными данными рассылки',
    )
    occurred_at: datetime | None = Field(default=None, description='Когда событие произошло')


class AcceptedEventSchema(Schema):
    """Ответ на приём события."""

    event_id: UUID = Field(description='Идентификатор принятого события')
    accepted: bool = Field(
        description='False означает, что такое событие уже принимали: письмо не задвоится',
    )


class TemplateSchema(Schema):
    """Шаблон письма."""

    id: UUID = Field(description='Идентификатор')
    code: str = Field(description='Код шаблона', examples=['welcome'])
    name: str = Field(description='Название для менеджера', examples=['Приветственное письмо'])
    channel: Channel = Field(description='Канал доставки')
    subject: str = Field(description='Тема письма', examples=['Добро пожаловать в Practix!'])
    body: str = Field(description='Тело письма: HTML с переменными Jinja2')
    version: int = Field(description='Версия: растёт при каждой правке')
    is_active: bool = Field(description='Выключенный шаблон не используется при рассылке')
    created_at: datetime = Field(description='Когда создан')
    updated_at: datetime = Field(description='Когда изменён')


class TemplateDraftSchema(BaseModel):
    """Создание или изменение шаблона."""

    code: str = Field(
        min_length=1, max_length=64, pattern=r'^[a-z][a-z0-9_]*$',
        description='Код шаблона: латиница в нижнем регистре, цифры и подчёркивание', examples=['welcome'],
    )
    name: str = Field(min_length=1, max_length=128, description='Название', examples=['Приветственное письмо'])
    channel: Channel = Field(default=Channel.EMAIL, description='Канал доставки')
    subject: str = Field(min_length=1, max_length=255, description='Тема', examples=['Добро пожаловать!'])
    body: str = Field(
        min_length=1,
        description='HTML с переменными Jinja2. Разрешённые переменные перечислены в описании эндпоинта',
        examples=['<h1>Привет, {{ first_name }}!</h1>'],
    )
    is_active: bool = Field(default=True, description='Использовать ли шаблон при рассылке')


class TemplatePreviewSchema(Schema):
    """Как письмо выглядит на тестовых данных."""

    subject: str = Field(description='Собранная тема')
    body: str = Field(description='Собранное тело')


class CampaignSchema(Schema):
    """Рассылка менеджера."""

    id: UUID = Field(description='Идентификатор')
    title: str = Field(description='Название', examples=['Недельная подборка, 40-я неделя'])
    template_code: str = Field(description='Код шаблона')
    channel: Channel = Field(description='Канал доставки')
    audience: Audience = Field(description='Кому')
    status: CampaignStatus = Field(description='Состояние')
    scheduled_at: datetime | None = Field(description='Когда запустить разовую рассылку')
    cron: str | None = Field(description='Расписание повторяемой рассылки', examples=['0 18 * * 5'])
    created_by: str | None = Field(description='Кто создал')
    created_at: datetime = Field(description='Когда создана')
    updated_at: datetime = Field(description='Когда изменена')


class CampaignDraftSchema(BaseModel):
    """Создание рассылки."""

    title: str = Field(min_length=1, max_length=255, description='Название')
    template_code: str = Field(min_length=1, max_length=64, description='Код шаблона')
    channel: Channel = Field(default=Channel.EMAIL, description='Канал доставки')
    audience: Audience = Field(description='Кому')
    scheduled_at: datetime | None = Field(
        default=None, description='Отложенный запуск: когда начать. Взаимоисключающе с cron',
    )
    cron: str | None = Field(
        default=None, max_length=128,
        description='Повторяемый запуск: расписание из пяти полей cron. Взаимоисключающе с scheduled_at',
        examples=['0 18 * * 5'],
    )
    context: dict[str, Any] = Field(default_factory=dict, description='Что подставить в шаблон')


class SubscriptionSchema(Schema):
    """Настройка уведомлений зрителя."""

    template_code: str = Field(description='Код типа уведомлений')
    channel: Channel = Field(description='Канал')
    enabled: bool = Field(description='Получать ли уведомления этого типа')
    updated_at: datetime = Field(description='Когда изменена')


class PreferencesSchema(Schema):
    """Настройки уведомлений зрителя."""

    unsubscribed_all: bool = Field(
        description='Зритель отписался от всего. Включение любого типа снимает этот признак',
    )
    items: list[SubscriptionSchema] = Field(
        description='Явно заданные настройки. Типа, которого здесь нет, зритель ещё не отключал',
    )


class EmailConfirmationSchema(Schema):
    """Подтверждённый адрес почты."""

    email: str | None = Field(description='Подтверждённый адрес', examples=['neo@example.com'])
    confirmed_at: datetime | None = Field(description='Когда подтверждён')


class SubscriptionUpdateSchema(BaseModel):
    """Включение или выключение типа уведомлений."""

    template_code: str = Field(min_length=1, max_length=64, description='Код типа уведомлений')
    channel: Channel = Field(default=Channel.EMAIL, description='Канал')
    enabled: bool = Field(description='Получать ли уведомления этого типа')


class DeliverySchema(Schema):
    """Уведомление в личном кабинете."""

    id: UUID = Field(description='Идентификатор')
    channel: Channel = Field(description='Канал')
    template_code: str = Field(description='Тип уведомления')
    subject: str = Field(description='Тема')
    status: DeliveryStatus = Field(description='Что стало с отправкой')
    created_at: datetime = Field(description='Когда создано')
    sent_at: datetime | None = Field(description='Когда отправлено')


class DeliveryPageSchema(Schema):
    """Страница уведомлений."""

    items: list[DeliverySchema] = Field(description='Уведомления, новые сверху')
    total: int = Field(description='Всего уведомлений')
    page_number: int = Field(description='Номер страницы')
    page_size: int = Field(description='Размер страницы')


class ShortenSchema(BaseModel):
    """Сокращение ссылки."""

    target_url: str = Field(min_length=1, max_length=2048, description='Длинный адрес')
    user_id: UUID | None = Field(default=None, description='Кому выдана ссылка: по нему считаются визиты')
    ttl_seconds: int | None = Field(default=None, ge=1, description='Сколько секунд ссылка действительна')
    purpose: str | None = Field(default=None, max_length=32, description='Служебная пометка')


class ShortLinkSchema(Schema):
    """Короткая ссылка."""

    key: str = Field(description='Ключ ссылки', examples=['b8NwYzA'])
    url: str = Field(description='Полный короткий адрес', examples=['http://localhost/s/b8NwYzA'])
    target_url: str = Field(description='Куда ведёт')
    expires_at: datetime | None = Field(description='До какого момента действует')


class HealthSchema(Schema):
    """Готовность сервиса."""

    status: str = Field(description='ok, когда сервис готов принимать запросы', examples=['ok'])
