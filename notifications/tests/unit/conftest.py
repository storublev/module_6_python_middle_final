"""Сервисы на хранилищах в памяти."""

import pytest

from services.assembly import AssemblyService, QuietHours
from services.campaigns import CampaignService
from services.ingest import IngestService
from services.planner import PlannerService
from services.renderer import Renderer
from services.sender import SenderService
from services.shortlinks import ShortLinkService
from services.subscriptions import SubscriptionService
from services.templates import TemplateService
from tests.unit import SECRET_KEY
from tests.unit.fakes import (
    Database,
    FakeCampaignRepository,
    FakeChannel,
    FakeContactDirectory,
    FakeDeliveryRepository,
    FakeEventStore,
    FakeNotificationRepository,
    FakePublisher,
    FakeShortLinkRepository,
    FakeSubscriptionRepository,
    FakeTemplateRepository,
)

BASE_URL = 'https://practix.local'
# Маленькая пачка, чтобы упереться в неё на трёх получателях.
BATCH_SIZE = 2


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
def events(db: Database) -> FakeEventStore:
    return FakeEventStore(db)


@pytest.fixture
def templates_repo(db: Database) -> FakeTemplateRepository:
    return FakeTemplateRepository(db)


@pytest.fixture
def subscriptions_repo(db: Database) -> FakeSubscriptionRepository:
    return FakeSubscriptionRepository(db)


@pytest.fixture
def notifications_repo(db: Database) -> FakeNotificationRepository:
    return FakeNotificationRepository(db)


@pytest.fixture
def deliveries_repo(db: Database) -> FakeDeliveryRepository:
    return FakeDeliveryRepository(db)


@pytest.fixture
def campaigns_repo(db: Database) -> FakeCampaignRepository:
    return FakeCampaignRepository(db)


@pytest.fixture
def links_repo(db: Database) -> FakeShortLinkRepository:
    return FakeShortLinkRepository(db)


@pytest.fixture
def directory() -> FakeContactDirectory:
    return FakeContactDirectory()


@pytest.fixture
def publisher() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel()


@pytest.fixture
def renderer() -> Renderer:
    return Renderer()


@pytest.fixture
def quiet_hours() -> QuietHours:
    # Окно через полночь — самый обычный случай и самый неудобный для проверок.
    return QuietHours(start_hour=21, end_hour=9, default_timezone='Europe/Moscow')


@pytest.fixture
def ingest(events: FakeEventStore, publisher: FakePublisher) -> IngestService:
    return IngestService(events, publisher)


@pytest.fixture
def planner(
    templates_repo: FakeTemplateRepository,
    subscriptions_repo: FakeSubscriptionRepository,
    notifications_repo: FakeNotificationRepository,
    directory: FakeContactDirectory,
    publisher: FakePublisher,
) -> PlannerService:
    return PlannerService(
        templates_repo, subscriptions_repo, notifications_repo, directory, publisher, BATCH_SIZE,
    )


@pytest.fixture
def assembly(
    templates_repo: FakeTemplateRepository,
    directory: FakeContactDirectory,
    renderer: Renderer,
    publisher: FakePublisher,
    quiet_hours: QuietHours,
) -> AssemblyService:
    return AssemblyService(templates_repo, directory, renderer, publisher, quiet_hours, BASE_URL)


@pytest.fixture
def sender(
    deliveries_repo: FakeDeliveryRepository,
    notifications_repo: FakeNotificationRepository,
    channel: FakeChannel,
) -> SenderService:
    return SenderService(deliveries_repo, notifications_repo, {channel.channel.value: channel})


@pytest.fixture
def campaigns(
    campaigns_repo: FakeCampaignRepository,
    templates_repo: FakeTemplateRepository,
    publisher: FakePublisher,
) -> CampaignService:
    return CampaignService(campaigns_repo, templates_repo, publisher)


@pytest.fixture
def subscriptions(subscriptions_repo: FakeSubscriptionRepository) -> SubscriptionService:
    return SubscriptionService(subscriptions_repo, SECRET_KEY)


@pytest.fixture
def template_service(templates_repo: FakeTemplateRepository, renderer: Renderer) -> TemplateService:
    return TemplateService(templates_repo, renderer)


@pytest.fixture
def shortlinks(links_repo: FakeShortLinkRepository) -> ShortLinkService:
    from datetime import timedelta

    return ShortLinkService(links_repo, BASE_URL, timedelta(days=3))
