"""Сервисы на хранилищах в памяти."""

from datetime import timedelta

import pytest

from services.assembly import AssemblyService, QuietHours
from services.campaigns import CampaignService
from services.confirmation import EmailConfirmationService
from services.ingest import IngestService
from services.planner import PlannerService
from services.renderer import InlineEngine, Renderer
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
    FakeEmailConfirmationRepository,
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
CONFIRM_TTL = timedelta(days=3)


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
def confirmations_repo(db: Database) -> FakeEmailConfirmationRepository:
    return FakeEmailConfirmationRepository(db)


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
def engine(renderer: Renderer) -> InlineEngine:
    # Сервисы получают сборку в том же процессе: изоляцию проверяют отдельные
    # тесты процесса сборки, а здесь проверяется бизнес-логика.
    return InlineEngine(renderer)


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
    engine: InlineEngine,
    publisher: FakePublisher,
    quiet_hours: QuietHours,
    confirmations: EmailConfirmationService,
) -> AssemblyService:
    return AssemblyService(
        templates_repo, directory, engine, publisher, quiet_hours, BASE_URL, SECRET_KEY, confirmations,
    )


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
def template_service(templates_repo: FakeTemplateRepository, engine: InlineEngine) -> TemplateService:
    return TemplateService(templates_repo, engine)


@pytest.fixture
def shortlinks(links_repo: FakeShortLinkRepository) -> ShortLinkService:
    return ShortLinkService(links_repo, BASE_URL)


@pytest.fixture
def confirmations(
    confirmations_repo: FakeEmailConfirmationRepository, shortlinks: ShortLinkService,
) -> EmailConfirmationService:
    return EmailConfirmationService(confirmations_repo, shortlinks, BASE_URL, CONFIRM_TTL)
