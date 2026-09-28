"""Сборка сервисов для эндпоинтов (Composition Root).

Только здесь выбираются конкретные реализации: PostgreSQL, RabbitMQ и
сервис авторизации по HTTP. Бизнес-логика получает их через конструктор и
зависит от интерфейсов из `storage/base.py`, поэтому ничего не знает ни о
SQLAlchemy, ни о aio-pika, ни о FastAPI.
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from db.postgres import get_session
from services.campaigns import CampaignService
from services.errors import ServiceTokenInvalidError
from services.ingest import IngestService
from services.shortlinks import ShortLinkService
from services.subscriptions import SubscriptionService
from services.templates import TemplateService
from storage.base import (
    CampaignRepository,
    DeliveryRepository,
    EventStore,
    MessagePublisher,
    ShortLinkRepository,
    SubscriptionRepository,
    TemplateRepository,
)
from storage.postgres import (
    PostgresCampaignRepository,
    PostgresDeliveryRepository,
    PostgresEventStore,
    PostgresShortLinkRepository,
    PostgresSubscriptionRepository,
    PostgresTemplateRepository,
)

DbSession = Annotated[AsyncSession, Depends(get_session)]

# Линтер принимает имя заголовка за пароль из-за слова token.
SERVICE_TOKEN_HEADER = 'X-Service-Token'  # noqa: S105


def get_publisher(request: Request) -> MessagePublisher:
    """Публикатор живёт всё время работы приложения: соединение с брокером одно."""
    return request.app.state.publisher


def get_events(session: DbSession) -> EventStore:
    return PostgresEventStore(session)


def get_templates_repo(session: DbSession) -> TemplateRepository:
    return PostgresTemplateRepository(session)


def get_subscriptions_repo(session: DbSession) -> SubscriptionRepository:
    return PostgresSubscriptionRepository(session)


def get_deliveries_repo(session: DbSession) -> DeliveryRepository:
    return PostgresDeliveryRepository(session)


def get_campaigns_repo(session: DbSession) -> CampaignRepository:
    return PostgresCampaignRepository(session)


def get_links_repo(session: DbSession) -> ShortLinkRepository:
    return PostgresShortLinkRepository(session)


Events = Annotated[EventStore, Depends(get_events)]
Templates = Annotated[TemplateRepository, Depends(get_templates_repo)]
Subscriptions = Annotated[SubscriptionRepository, Depends(get_subscriptions_repo)]
Deliveries = Annotated[DeliveryRepository, Depends(get_deliveries_repo)]
Campaigns = Annotated[CampaignRepository, Depends(get_campaigns_repo)]
Links = Annotated[ShortLinkRepository, Depends(get_links_repo)]
Publisher = Annotated[MessagePublisher, Depends(get_publisher)]


def get_ingest_service(events: Events, publisher: Publisher) -> IngestService:
    return IngestService(events, publisher)


def get_template_service(request: Request, templates: Templates) -> TemplateService:
    return TemplateService(templates, request.app.state.renderer)


def get_campaign_service(campaigns: Campaigns, templates: Templates, publisher: Publisher) -> CampaignService:
    return CampaignService(campaigns, templates, publisher)


def get_subscription_service(subscriptions: Subscriptions) -> SubscriptionService:
    return SubscriptionService(subscriptions, settings.jwt_secret_key.get_secret_value())


def get_shortlink_service(links: Links) -> ShortLinkService:
    return ShortLinkService(links, settings.public_base_url, settings.confirm_link_ttl)


IngestServiceDep = Annotated[IngestService, Depends(get_ingest_service)]
TemplateServiceDep = Annotated[TemplateService, Depends(get_template_service)]
CampaignServiceDep = Annotated[CampaignService, Depends(get_campaign_service)]
SubscriptionServiceDep = Annotated[SubscriptionService, Depends(get_subscription_service)]
ShortLinkServiceDep = Annotated[ShortLinkService, Depends(get_shortlink_service)]


def require_service_token(
    token: Annotated[
        str | None,
        Header(alias=SERVICE_TOKEN_HEADER, description='Общий секрет служебного доступа'),
    ] = None,
) -> None:
    """Пускает только сервисы кинотеатра и админ-панель.

    События и рассылки приходят не от пользователя, а от других частей
    системы: своего access-токена у них нет. Сравнение постоянного времени —
    чтобы секрет нельзя было подобрать по времени ответа.

    Raises:
        ServiceTokenInvalidError: секрет не задан или не совпал.
    """
    expected = settings.auth_service_token.get_secret_value()
    # Байты, а не строки: `compare_digest` на не-ASCII строке поднимает
    # TypeError, и заголовок с кириллицей давал бы 500 вместо 401.
    if not expected or not token or not secrets.compare_digest(token.encode('utf-8'), expected.encode('utf-8')):
        raise ServiceTokenInvalidError


ServiceToken = Annotated[None, Depends(require_service_token)]
