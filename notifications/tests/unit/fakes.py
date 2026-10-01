"""Хранилища в памяти для unit-тестов.

Бизнес-логика зависит от интерфейсов `storage/base.py`, поэтому её можно
проверить целиком без PostgreSQL, RabbitMQ и почтового сервера. Так же
устроены тесты остальных сервисов кинотеатра.

Заглушки повторяют не форму таблиц, а **поведение контракта**: например,
`remember` возвращает False на повтор, а `claim` — BUSY, пока идёт чужая аренда.
Если заглушка будет мягче настоящего хранилища, тесты пройдут там, где
рабочий код упадёт.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from channels.base import ChannelUnavailableError, DeliveryChannel, MessageRejectedError
from models.campaign import Campaign, CampaignDraft
from models.enums import CampaignStatus, Channel, ClaimState, DeliveryStatus
from models.event import Audience, Event
from models.notification import (
    Delivery,
    DeliveryClaim,
    EmailConfirmation,
    NotificationRecord,
    Page,
    Recipient,
    RenderedMessage,
    ShortLink,
    Subscription,
    Template,
    TemplateDraft,
)
from models.outbox import OutboxDraft, OutboxMessage
from storage.base import (
    AlreadyExistsError,
    CampaignRepository,
    ContactDirectory,
    DeliveryRepository,
    EmailConfirmationRepository,
    EventStore,
    MessagePublisher,
    NotificationRepository,
    Outbox,
    ShortLinkRepository,
    StorageUnavailableError,
    SubscriptionRepository,
    TemplateRepository,
)


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Database:
    """Общее состояние заглушек — как одна база на все репозитории."""

    events: dict[UUID, Event] = field(default_factory=dict)
    templates: dict[str, Template] = field(default_factory=dict)
    template_versions: dict[tuple[str, int], Template] = field(default_factory=dict)
    subscriptions: dict[tuple[UUID, str, str], Subscription] = field(default_factory=dict)
    notifications: dict[tuple[UUID, str, str], NotificationRecord] = field(default_factory=dict)
    deliveries: dict[str, Delivery] = field(default_factory=dict)
    # Аренды отправок: ключ идемпотентности → до какого момента держат.
    delivery_leases: dict[str, datetime] = field(default_factory=dict)
    campaigns: dict[UUID, Campaign] = field(default_factory=dict)
    campaign_context: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    campaign_runs: set[tuple[UUID, str]] = field(default_factory=set)
    links: dict[str, ShortLink] = field(default_factory=dict)
    # Кто отписался от всего: у такого зрителя записей подписок может не быть
    # вовсе, а писать ему всё равно нельзя.
    unsubscribed: set[UUID] = field(default_factory=set)
    # Токены подтверждения почты по хешу и подтверждённые адреса.
    confirmation_tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    confirmations: dict[UUID, EmailConfirmation] = field(default_factory=dict)
    # Задания outbox: идентификатор → (задание, с какого момента брать, попытки).
    outbox: dict[UUID, dict[str, Any]] = field(default_factory=dict)

    def add_outbox(self, publication: OutboxDraft) -> None:
        self.outbox[uuid4()] = {
            'publication': publication,
            'available_at': publication.available_at or datetime.min.replace(tzinfo=timezone.utc),
            'attempts': 0,
        }

    def outbox_of(self, stage: str) -> list[dict[str, Any]]:
        """Задания этапа, ещё не перенесённые в брокер."""
        return [item['publication'].payload for item in self.outbox.values() if item['publication'].stage == stage]


class FakeEventStore(EventStore):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def remember(self, event: Event, publication: OutboxDraft) -> bool:
        if event.event_id in self.db.events:
            return False
        self.db.events[event.event_id] = event
        self.db.add_outbox(publication)
        return True


class FakeOutbox(Outbox):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def put(self, publication: OutboxDraft) -> None:
        self.db.add_outbox(publication)

    async def claim(self, limit: int, lease: timedelta, now: datetime) -> list[OutboxMessage]:
        due = sorted(
            (item for item in self.db.outbox.items() if item[1]['available_at'] <= now),
            key=lambda item: item[1]['available_at'],
        )[:limit]
        claimed = []
        for message_id, item in due:
            item['available_at'] = now + lease
            item['attempts'] += 1
            publication = item['publication']
            claimed.append(OutboxMessage(
                id=message_id, stage=publication.stage, payload=publication.payload,
                request_id=publication.request_id, attempts=item['attempts'],
            ))
        return claimed

    async def done(self, message_id: UUID) -> None:
        self.db.outbox.pop(message_id, None)

    async def retry(self, message_id: UUID, at: datetime, error: str) -> None:
        self.db.outbox[message_id]['available_at'] = at


class FakeTemplateRepository(TemplateRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, code: str) -> Template | None:
        return self.db.templates.get(code)

    async def get_version(self, code: str, version: int) -> Template | None:
        return self.db.template_versions.get((code, version))

    async def list_all(self) -> list[Template]:
        return sorted(self.db.templates.values(), key=lambda item: item.code)

    async def create(self, draft: TemplateDraft) -> Template:
        if draft.code in self.db.templates:
            raise AlreadyExistsError(draft.code)
        template = Template(
            id=uuid4(), code=draft.code, name=draft.name, channel=draft.channel,
            subject=draft.subject, body=draft.body, version=1, is_active=draft.is_active,
            created_at=now(), updated_at=now(),
        )
        self.db.templates[draft.code] = template
        self.db.template_versions[(draft.code, 1)] = template
        return template

    async def update(self, code: str, draft: TemplateDraft) -> Template | None:
        current = self.db.templates.get(code)
        if current is None:
            return None
        updated = current.model_copy(update={
            'name': draft.name, 'subject': draft.subject, 'body': draft.body,
            'channel': draft.channel, 'is_active': draft.is_active,
            'version': current.version + 1, 'updated_at': now(),
        })
        self.db.templates[code] = updated
        self.db.template_versions[(code, updated.version)] = updated
        return updated

    async def delete(self, code: str) -> bool:
        return self.db.templates.pop(code, None) is not None


class FakeSubscriptionRepository(SubscriptionRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list_for_user(self, user_id: UUID) -> list[Subscription]:
        return [item for key, item in self.db.subscriptions.items() if key[0] == user_id]

    async def set_enabled(
        self, user_id: UUID, template_code: str, channel: Channel, enabled: bool,
    ) -> Subscription:
        if enabled:
            self.db.unsubscribed.discard(user_id)
        subscription = Subscription(
            user_id=user_id, template_code=template_code, channel=channel, enabled=enabled, updated_at=now(),
        )
        self.db.subscriptions[(user_id, template_code, channel.value)] = subscription
        return subscription

    async def unsubscribe_all(self, user_id: UUID) -> None:
        self.db.unsubscribed.add(user_id)
        for key, item in list(self.db.subscriptions.items()):
            if key[0] == user_id:
                self.db.subscriptions[key] = item.model_copy(update={'enabled': False})

    async def is_unsubscribed(self, user_ids: Sequence[UUID]) -> set[UUID]:
        return {user_id for user_id in user_ids if user_id in self.db.unsubscribed}

    async def filter_enabled(
        self, user_ids: Sequence[UUID], template_code: str, channel: Channel,
    ) -> set[UUID]:
        disabled = {
            key[0] for key, item in self.db.subscriptions.items()
            if key[1] == template_code and key[2] == channel.value and not item.enabled
        }
        disabled |= self.db.unsubscribed
        return {user_id for user_id in user_ids if user_id not in disabled}


class FakeNotificationRepository(NotificationRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def filter_outdated(
        self, user_ids: Sequence[UUID], template_code: str, content_id: str, content_version: int | None,
    ) -> set[UUID]:
        if content_version is None:
            return set(user_ids)
        fresh = set()
        for user_id in user_ids:
            record = self.db.notifications.get((user_id, template_code, content_id))
            if record is None or record.content_version is None or record.content_version < content_version:
                fresh.add(user_id)
        return fresh

    async def mark_notified(
        self, user_id: UUID, template_code: str, content_id: str, content_version: int | None, at: datetime,
    ) -> None:
        self.db.notifications[(user_id, template_code, content_id)] = NotificationRecord(
            id=uuid4(), user_id=user_id, template_code=template_code, content_id=content_id,
            content_version=content_version, last_sent_at=at,
        )

    async def get(self, user_id: UUID, template_code: str, content_id: str) -> NotificationRecord | None:
        return self.db.notifications.get((user_id, template_code, content_id))


class FakeDeliveryRepository(DeliveryRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def claim(self, message: RenderedMessage, lease: timedelta, now: datetime) -> DeliveryClaim:
        key = message.idempotency_key
        delivery = self.db.deliveries.get(key)
        if delivery is None:
            self.db.deliveries[key] = Delivery(
                id=uuid4(), idempotency_key=key, user_id=message.user_id,
                channel=message.channel, template_code=message.template_code, subject=message.subject,
                status=DeliveryStatus.PENDING, error=None, created_at=now, sent_at=None,
            )
            self.db.delivery_leases[key] = now + lease
            return DeliveryClaim(state=ClaimState.CLAIMED)
        if delivery.status is not DeliveryStatus.PENDING:
            return DeliveryClaim(state=ClaimState.DONE)
        held_until = self.db.delivery_leases.get(key)
        if held_until is not None and held_until > now:
            return DeliveryClaim(state=ClaimState.BUSY)
        self.db.delivery_leases[key] = now + lease
        return DeliveryClaim(state=ClaimState.CLAIMED, recovered=held_until is not None)

    async def finish(self, idempotency_key: str, status: DeliveryStatus, error: str | None = None) -> None:
        delivery = self.db.deliveries.get(idempotency_key)
        if delivery is None:
            return
        self.db.delivery_leases.pop(idempotency_key, None)
        self.db.deliveries[idempotency_key] = delivery.model_copy(update={
            'status': status, 'error': error, 'sent_at': now() if status is DeliveryStatus.SENT else None,
        })

    async def release(self, idempotency_key: str, error: str) -> None:
        delivery = self.db.deliveries.get(idempotency_key)
        if delivery is not None and delivery.status is DeliveryStatus.PENDING:
            self.db.delivery_leases.pop(idempotency_key, None)
            self.db.deliveries[idempotency_key] = delivery.model_copy(update={'error': error})

    async def list_for_user(self, user_id: UUID, page_number: int, page_size: int) -> Page[Delivery]:
        items = sorted(
            (item for item in self.db.deliveries.values() if item.user_id == user_id),
            key=lambda item: item.created_at, reverse=True,
        )
        start = (page_number - 1) * page_size
        return Page[Delivery](
            items=items[start:start + page_size], total=len(items),
            page_number=page_number, page_size=page_size,
        )


class FakeCampaignRepository(CampaignRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, draft: CampaignDraft, created_by: str | None) -> Campaign:
        status = CampaignStatus.SCHEDULED if (draft.scheduled_at or draft.cron) else CampaignStatus.RUNNING
        campaign = Campaign(
            id=uuid4(), title=draft.title, template_code=draft.template_code, channel=draft.channel,
            audience=draft.audience, status=status, scheduled_at=draft.scheduled_at, cron=draft.cron,
            created_by=created_by, created_at=now(), updated_at=now(),
        )
        self.db.campaigns[campaign.id] = campaign
        self.db.campaign_context[campaign.id] = dict(draft.context)
        return campaign

    async def get(self, campaign_id: UUID) -> Campaign | None:
        return self.db.campaigns.get(campaign_id)

    async def list_all(self) -> list[Campaign]:
        return sorted(self.db.campaigns.values(), key=lambda item: item.created_at, reverse=True)

    async def set_status(self, campaign_id: UUID, status: str) -> Campaign | None:
        campaign = self.db.campaigns.get(campaign_id)
        if campaign is None:
            return None
        updated = campaign.model_copy(update={'status': CampaignStatus(status), 'updated_at': now()})
        self.db.campaigns[campaign_id] = updated
        return updated

    async def due(self, moment: datetime) -> list[Campaign]:
        return [
            campaign for campaign in self.db.campaigns.values()
            if campaign.status in (CampaignStatus.SCHEDULED, CampaignStatus.RUNNING)
            and (campaign.cron or campaign.scheduled_at is None or campaign.scheduled_at <= moment)
        ]

    async def claim_run(
        self, campaign_id: UUID, period_key: str, event_id: UUID, publication: OutboxDraft, finish: bool,
    ) -> bool:
        key = (campaign_id, period_key)
        if key in self.db.campaign_runs:
            return False
        self.db.campaign_runs.add(key)
        self.db.add_outbox(publication)
        if finish:
            await self.set_status(campaign_id, CampaignStatus.DONE.value)
        return True

    async def context_of(self, campaign_id: UUID) -> dict[str, Any]:
        return dict(self.db.campaign_context.get(campaign_id, {}))


class FakeShortLinkRepository(ShortLinkRepository):
    def __init__(self, db: Database) -> None:
        self.db = db
        self._counter = 0

    async def create(
        self, target_url: str, user_id: UUID | None, expires_at: datetime | None, purpose: str | None,
    ) -> ShortLink:
        self._counter += 1
        key = f'key{self._counter:04d}'
        link = ShortLink(
            key=key, target_url=target_url, user_id=user_id, expires_at=expires_at,
            visits=0, purpose=purpose, created_at=now(),
        )
        self.db.links[key] = link
        return link

    async def resolve(self, key: str, at: datetime) -> ShortLink | None:
        link = self.db.links.get(key)
        if link is None:
            return None
        self.db.links[key] = link.model_copy(update={'visits': link.visits + 1})
        if link.expires_at is not None and link.expires_at <= at:
            return None
        return self.db.links[key]


class FakeEmailConfirmationRepository(EmailConfirmationRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def issue(self, token_hash: str, user_id: UUID, email: str, expires_at: datetime) -> None:
        self.db.confirmation_tokens[token_hash] = {
            'user_id': user_id, 'email': email, 'expires_at': expires_at, 'used_at': None,
        }

    async def confirm(self, token_hash: str, at: datetime) -> EmailConfirmation | None:
        token = self.db.confirmation_tokens.get(token_hash)
        if token is None or token['used_at'] is not None or token['expires_at'] <= at:
            return None
        token['used_at'] = at
        confirmation = EmailConfirmation(user_id=token['user_id'], email=token['email'], confirmed_at=at)
        self.db.confirmations[confirmation.user_id] = confirmation
        return confirmation

    async def get(self, user_id: UUID) -> EmailConfirmation | None:
        return self.db.confirmations.get(user_id)


class FakeContactDirectory(ContactDirectory):
    """Справочник контактов с подсчётом обращений.

    Счётчик — не украшение: тем, что на пачку приходится **один** запрос, и
    отличается работающая рассылка от той, что кладёт сервис авторизации.
    """

    def __init__(self, recipients: dict[UUID, Recipient] | None = None) -> None:
        self.recipients = recipients or {}
        self.calls = 0
        self.fail_with: Exception | None = None

    async def contacts(self, user_ids: Sequence[UUID]) -> list[Recipient]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return [self.recipients[user_id] for user_id in user_ids if user_id in self.recipients]

    async def page(self, after_id: UUID | None, limit: int) -> tuple[list[Recipient], UUID | None]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        ordered = sorted(self.recipients.values(), key=lambda item: str(item.user_id))
        if after_id is not None:
            ordered = [item for item in ordered if str(item.user_id) > str(after_id)]
        page = ordered[:limit]
        next_after = page[-1].user_id if len(page) == limit and len(ordered) > limit else None
        return page, next_after


class FakePublisher(MessagePublisher):
    """Запоминает опубликованные сообщения по этапам."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any], str]] = []
        self.fail_with: Exception | None = None

    async def publish(self, stage: str, payload: dict[str, Any], request_id: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.messages.append((stage, payload, request_id))

    def of(self, stage: str) -> list[dict[str, Any]]:
        return [payload for published_stage, payload, _ in self.messages if published_stage == stage]


class FakeChannel(DeliveryChannel):
    """Канал доставки, который можно заставить сломаться."""

    def __init__(self, channel: Channel = Channel.EMAIL) -> None:
        self._channel = channel
        self.sent: list[RenderedMessage] = []
        self.unavailable_times = 0
        self.reject = False

    @property
    def channel(self) -> Channel:
        return self._channel

    async def send(self, message: RenderedMessage) -> None:
        if self.reject:
            raise MessageRejectedError('Адрес отклонён')
        if self.unavailable_times > 0:
            self.unavailable_times -= 1
            raise ChannelUnavailableError('Почтовый сервер не отвечает')
        self.sent.append(message)


def recipient(user_id: UUID | None = None, **overrides: Any) -> Recipient:
    """Получатель со значениями по умолчанию."""
    data: dict[str, Any] = {
        'user_id': user_id or uuid4(),
        'email': 'viewer@example.com',
        'first_name': 'Томас',
        'last_name': 'Андерсон',
        'timezone': 'Europe/Moscow',
    }
    data.update(overrides)
    return Recipient(**data)


def template(code: str = 'welcome', **overrides: Any) -> Template:
    """Шаблон со значениями по умолчанию."""
    data: dict[str, Any] = {
        'id': uuid4(), 'code': code, 'name': 'Письмо', 'channel': Channel.EMAIL,
        'subject': 'Привет, {{ first_name }}!', 'body': '<p>Здравствуйте, {{ full_name }}</p>',
        'version': 1, 'is_active': True, 'created_at': now(), 'updated_at': now(),
    }
    data.update(overrides)
    return Template(**data)


def event(**overrides: Any) -> Event:
    """Событие со значениями по умолчанию."""
    data: dict[str, Any] = {
        'event_id': uuid4(),
        'routing_key': 'film-reporting.v1.episode-added',
        'template_code': 'new_episode',
        'audience': Audience(kind='users', user_ids=[uuid4()]),
    }
    data.update(overrides)
    return Event(**data)


class BrokenRepository:
    """Хранилище, которое всегда отвечает отказом."""

    def __getattr__(self, _: str) -> Any:
        async def fail(*args: Any, **kwargs: Any) -> Any:
            raise StorageUnavailableError('Хранилище недоступно')

        return fail
