"""Реализации хранилищ на PostgreSQL.

Сбой базы выходит наружу как `StorageUnavailableError`: исключения SQLAlchemy
и asyncpg за пределы этого модуля не уходят — иначе бизнес-логика узнала бы,
какая под ней база, и поменять её стало бы нельзя.
"""

import logging
import secrets
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.campaign import Campaign, CampaignDraft
from models.enums import CampaignStatus, Channel, ClaimState, DeliveryStatus
from models.event import Event
from models.notification import (
    Delivery,
    DeliveryClaim,
    EmailConfirmation,
    NotificationRecord,
    Page,
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
    DeliveryRepository,
    EmailConfirmationRepository,
    EventStore,
    NotificationRepository,
    Outbox,
    ShortLinkRepository,
    StorageUnavailableError,
    SubscriptionRepository,
    TemplateRepository,
)
from storage.orm import (
    CampaignRow,
    CampaignRunRow,
    DeliveryRow,
    EmailConfirmationRow,
    EmailConfirmationTokenRow,
    EventRow,
    NotificationRow,
    OutboxRow,
    ShortLinkRow,
    SubscriptionRow,
    TemplateRow,
    TemplateVersionRow,
    UserPreferenceRow,
)

logger = logging.getLogger(__name__)

# Длина ключа короткой ссылки. Семь символов из 62 — это 3,5 × 10¹², чего
# хватает с запасом, а ссылка остаётся короткой, ради чего всё и затевалось.
SHORT_KEY_LENGTH = 7
SHORT_KEY_ALPHABET = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
# Сколько раз пробовать другой ключ при столкновении. Столкновение на таком
# пространстве — редкость, но молча отдавать чужую ссылку нельзя.
SHORT_KEY_ATTEMPTS = 5


class PostgresRepository:
    """Общая часть: сессия и перевод сбоев базы в контракт хранилища."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def _errors(self) -> AsyncIterator[None]:
        try:
            yield
        except (OperationalError, InterfaceError) as error:
            await self._rollback()
            raise StorageUnavailableError(str(error)) from error
        except IntegrityError:
            await self._rollback()
            raise

    async def _rollback(self) -> None:
        try:
            await self.session.rollback()
        except Exception as error:  # noqa: BLE001 - откат после сбоя сам может не пройти
            # Соединение уже оборвано: откатывать нечего и некому. Наружу
            # уйдёт исходная причина сбоя, а не эта.
            logger.debug('Откат транзакции не удался: %s', error)


def _outbox_row(publication: OutboxDraft) -> OutboxRow:
    row = OutboxRow(stage=publication.stage, payload=publication.payload, request_id=publication.request_id)
    if publication.available_at is not None:
        row.available_at = publication.available_at
    return row


class PostgresEventStore(PostgresRepository, EventStore):
    async def remember(self, event: Event, publication: OutboxDraft) -> bool:
        # ON CONFLICT DO NOTHING, а не «проверить и вставить»: между проверкой
        # и вставкой успевает пройти соперник, и тогда события задвоятся.
        query = (
            pg_insert(EventRow)
            .values(
                event_id=event.event_id,
                routing_key=event.routing_key,
                template_code=event.template_code,
                payload=event.model_dump(mode='json'),
            )
            .on_conflict_do_nothing(index_elements=[EventRow.event_id])
            .returning(EventRow.event_id)
        )
        async with self._errors():
            inserted = await self.session.scalar(query)
            if inserted is not None:
                # Задание на публикацию — в той же транзакции: событие без
                # задания (принято, но в очередь не попало) невозможно.
                self.session.add(_outbox_row(publication))
            await self.session.commit()
        return inserted is not None


class PostgresOutbox(PostgresRepository, Outbox):
    async def put(self, publication: OutboxDraft) -> None:
        async with self._errors():
            self.session.add(_outbox_row(publication))
            await self.session.commit()

    async def claim(self, limit: int, lease: timedelta, now: datetime) -> list[OutboxMessage]:
        # SKIP LOCKED: два ретранслятора разбирают разные задания, а не ждут
        # друг друга на одних и тех же строках. Аренда — тот же available_at,
        # сдвинутый вперёд: отдельная колонка «кто взял» не нужна.
        due = (
            select(OutboxRow.id)
            .where(OutboxRow.available_at <= now)
            .order_by(OutboxRow.available_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        query = (
            update(OutboxRow)
            .where(OutboxRow.id.in_(due.scalar_subquery()))
            .values(available_at=now + lease, attempts=OutboxRow.attempts + 1)
            .returning(OutboxRow)
        )
        async with self._errors():
            rows = (await self.session.scalars(query)).all()
            messages = [OutboxMessage.model_validate(row) for row in rows]
            await self.session.commit()
        return messages

    async def done(self, message_id: UUID) -> None:
        async with self._errors():
            await self.session.execute(delete(OutboxRow).where(OutboxRow.id == message_id))
            await self.session.commit()

    async def retry(self, message_id: UUID, at: datetime, error: str) -> None:
        async with self._errors():
            await self.session.execute(
                update(OutboxRow).where(OutboxRow.id == message_id).values(available_at=at, last_error=error),
            )
            await self.session.commit()


class PostgresTemplateRepository(PostgresRepository, TemplateRepository):
    async def get(self, code: str) -> Template | None:
        async with self._errors():
            row = await self.session.scalar(select(TemplateRow).where(TemplateRow.code == code))
        return Template.model_validate(row) if row else None

    async def get_version(self, code: str, version: int) -> Template | None:
        current = await self.get(code)
        if current is not None and current.version == version:
            return current
        query = select(TemplateVersionRow).where(
            TemplateVersionRow.code == code, TemplateVersionRow.version == version,
        )
        async with self._errors():
            row = await self.session.scalar(query)
        if row is None:
            return None
        # У архивной версии нет собственных отметок времени действующей
        # записи, поэтому недостающие поля берутся из неё.
        return Template(
            id=row.id, code=row.code, name=row.name, channel=Channel(row.channel),
            subject=row.subject, body=row.body, version=row.version, is_active=False,
            created_at=row.created_at, updated_at=row.created_at,
        )

    async def list_all(self) -> list[Template]:
        async with self._errors():
            rows = (await self.session.scalars(select(TemplateRow).order_by(TemplateRow.code))).all()
        return [Template.model_validate(row) for row in rows]

    async def create(self, draft: TemplateDraft) -> Template:
        async with self._errors():
            try:
                row = TemplateRow(
                    code=draft.code, name=draft.name, channel=draft.channel.value,
                    subject=draft.subject, body=draft.body, is_active=draft.is_active, version=1,
                )
                self.session.add(row)
                await self.session.flush()
                self.session.add(_version_of(row))
                await self.session.commit()
            except IntegrityError as error:
                raise AlreadyExistsError(draft.code) from error
        await self.session.refresh(row)
        return Template.model_validate(row)

    async def update(self, code: str, draft: TemplateDraft) -> Template | None:
        async with self._errors():
            row = await self.session.scalar(select(TemplateRow).where(TemplateRow.code == code))
            if row is None:
                return None
            row.name, row.subject, row.body = draft.name, draft.subject, draft.body
            row.channel, row.is_active = draft.channel.value, draft.is_active
            # Версия растёт на каждую правку: по ней рассылка, начатая со
            # старым текстом, досылается старым текстом.
            row.version += 1
            self.session.add(_version_of(row))
            await self.session.commit()
            await self.session.refresh(row)
        return Template.model_validate(row)

    async def delete(self, code: str) -> bool:
        async with self._errors():
            # RETURNING вместо rowcount: у асинхронного результата SQLAlchemy
            # его в типах нет, а нам всё равно нужен ответ «была ли строка».
            removed = await self.session.scalar(
                delete(TemplateRow).where(TemplateRow.code == code).returning(TemplateRow.id),
            )
            await self.session.commit()
        return removed is not None


def _version_of(row: TemplateRow) -> TemplateVersionRow:
    return TemplateVersionRow(
        code=row.code, version=row.version, name=row.name,
        channel=row.channel, subject=row.subject, body=row.body,
    )


class PostgresSubscriptionRepository(PostgresRepository, SubscriptionRepository):
    async def list_for_user(self, user_id: UUID) -> list[Subscription]:
        query = select(SubscriptionRow).where(SubscriptionRow.user_id == user_id).order_by(
            SubscriptionRow.template_code, SubscriptionRow.channel,
        )
        async with self._errors():
            rows = (await self.session.scalars(query)).all()
        return [_subscription(row) for row in rows]

    async def set_enabled(
        self, user_id: UUID, template_code: str, channel: Channel, enabled: bool,
    ) -> Subscription:
        # Включение любого типа снимает общий отказ: иначе зритель, однажды
        # отписавшийся, не смог бы вернуться.
        if enabled:
            await self._clear_global_optout(user_id)
        query = (
            pg_insert(SubscriptionRow)
            .values(user_id=user_id, template_code=template_code, channel=channel.value, enabled=enabled)
            .on_conflict_do_update(
                index_elements=[SubscriptionRow.user_id, SubscriptionRow.template_code, SubscriptionRow.channel],
                set_={'enabled': enabled, 'updated_at': func.now()},
            )
            .returning(SubscriptionRow)
        )
        async with self._errors():
            row = await self.session.scalar(query)
            await self.session.commit()
        # RETURNING у upsert всегда отдаёт строку: конфликт разрешается
        # обновлением, а не пропуском.
        assert row is not None  # noqa: S101
        return _subscription(row)

    async def unsubscribe_all(self, user_id: UUID) -> None:
        mark = (
            pg_insert(UserPreferenceRow)
            .values(user_id=user_id, unsubscribed_all=True)
            .on_conflict_do_update(
                index_elements=[UserPreferenceRow.user_id],
                set_={'unsubscribed_all': True, 'updated_at': func.now()},
            )
        )
        async with self._errors():
            # Общий признак — главное: у зрителя, который никогда ничего не
            # настраивал, записей подписок нет, и выключать было бы нечего.
            await self.session.execute(mark)
            await self.session.execute(
                update(SubscriptionRow).where(SubscriptionRow.user_id == user_id).values(enabled=False),
            )
            await self.session.commit()

    async def is_unsubscribed(self, user_ids: Sequence[UUID]) -> set[UUID]:
        if not user_ids:
            return set()
        query = select(UserPreferenceRow.user_id).where(
            UserPreferenceRow.user_id.in_(user_ids),
            UserPreferenceRow.unsubscribed_all.is_(True),
        )
        async with self._errors():
            return set((await self.session.scalars(query)).all())

    async def filter_enabled(
        self, user_ids: Sequence[UUID], template_code: str, channel: Channel,
    ) -> set[UUID]:
        if not user_ids:
            return set()
        # Запрашиваем только отказы: их на порядки меньше, чем согласий,
        # потому что отсутствие записи и есть согласие.
        query = select(SubscriptionRow.user_id).where(
            SubscriptionRow.user_id.in_(user_ids),
            SubscriptionRow.template_code == template_code,
            SubscriptionRow.channel == channel.value,
            SubscriptionRow.enabled.is_(False),
        )
        async with self._errors():
            disabled = set((await self.session.scalars(query)).all())
        # Отписавшиеся от всего отсеиваются тем же проходом: иначе отписка из
        # письма не значила бы ничего для зрителя без явных настроек.
        disabled |= await self.is_unsubscribed(user_ids)
        return {user_id for user_id in user_ids if user_id not in disabled}

    async def _clear_global_optout(self, user_id: UUID) -> None:
        async with self._errors():
            await self.session.execute(
                update(UserPreferenceRow)
                .where(UserPreferenceRow.user_id == user_id)
                .values(unsubscribed_all=False),
            )
            await self.session.commit()


def _subscription(row: SubscriptionRow) -> Subscription:
    return Subscription(
        user_id=row.user_id, template_code=row.template_code,
        channel=Channel(row.channel), enabled=row.enabled, updated_at=row.updated_at,
    )


class PostgresNotificationRepository(PostgresRepository, NotificationRepository):
    async def filter_outdated(
        self, user_ids: Sequence[UUID], template_code: str, content_id: str, content_version: int | None,
    ) -> set[UUID]:
        if not user_ids:
            return set()
        if content_version is None:
            # Версии нет — сверять нечего, письмо уместно каждому.
            return set(user_ids)
        query = select(NotificationRow.user_id).where(
            NotificationRow.user_id.in_(user_ids),
            NotificationRow.template_code == template_code,
            NotificationRow.content_id == content_id,
            NotificationRow.content_version >= content_version,
        )
        async with self._errors():
            already = set((await self.session.scalars(query)).all())
        return {user_id for user_id in user_ids if user_id not in already}

    async def mark_notified(
        self, user_id: UUID, template_code: str, content_id: str, content_version: int | None, at: datetime,
    ) -> None:
        query = (
            pg_insert(NotificationRow)
            .values(
                user_id=user_id, template_code=template_code, content_id=content_id,
                content_version=content_version, last_sent_at=at,
            )
            .on_conflict_do_update(
                index_elements=[NotificationRow.user_id, NotificationRow.template_code, NotificationRow.content_id],
                set_={'content_version': content_version, 'last_sent_at': at},
            )
        )
        async with self._errors():
            await self.session.execute(query)
            await self.session.commit()

    async def get(self, user_id: UUID, template_code: str, content_id: str) -> NotificationRecord | None:
        query = select(NotificationRow).where(
            NotificationRow.user_id == user_id,
            NotificationRow.template_code == template_code,
            NotificationRow.content_id == content_id,
        )
        async with self._errors():
            row = await self.session.scalar(query)
        return NotificationRecord.model_validate(row) if row else None


class PostgresDeliveryRepository(PostgresRepository, DeliveryRepository):
    async def claim(self, message: RenderedMessage, lease: timedelta, now: datetime) -> DeliveryClaim:
        locked_until = now + lease
        create = (
            pg_insert(DeliveryRow)
            .values(
                idempotency_key=message.idempotency_key,
                user_id=message.user_id,
                channel=message.channel.value,
                template_code=message.template_code,
                subject=message.subject[:255],
                status=DeliveryStatus.PENDING.value,
                locked_until=locked_until,
                attempts=1,
            )
            .on_conflict_do_nothing(index_elements=[DeliveryRow.idempotency_key])
            .returning(DeliveryRow.id)
        )
        # Чужая запись читается с блокировкой строки: из нескольких
        # отправителей, одновременно увидевших вышедшую аренду, решение примет
        # один, а остальные дождутся его и увидят уже новую аренду.
        current = (
            select(DeliveryRow.status, DeliveryRow.locked_until)
            .where(DeliveryRow.idempotency_key == message.idempotency_key)
            .with_for_update()
        )
        async with self._errors():
            if await self.session.scalar(create) is not None:
                await self.session.commit()
                return DeliveryClaim(state=ClaimState.CLAIMED)
            row = (await self.session.execute(current)).one()
            status, held_until = row.status, row.locked_until
            if status != DeliveryStatus.PENDING.value:
                await self.session.commit()
                return DeliveryClaim(state=ClaimState.DONE)
            if held_until is not None and held_until > now:
                await self.session.commit()
                return DeliveryClaim(state=ClaimState.BUSY)
            await self.session.execute(
                update(DeliveryRow)
                .where(DeliveryRow.idempotency_key == message.idempotency_key)
                .values(locked_until=locked_until, attempts=DeliveryRow.attempts + 1),
            )
            await self.session.commit()
        # Аренда была, но вышла без итога: прошлый держатель пропал, и исход
        # его попытки неизвестен. Пустая аренда — прошлая попытка честно
        # закончилась временной ошибкой.
        return DeliveryClaim(state=ClaimState.CLAIMED, recovered=held_until is not None)

    async def finish(self, idempotency_key: str, status: DeliveryStatus, error: str | None = None) -> None:
        values: dict[str, Any] = {'status': status.value, 'error': error, 'locked_until': None}
        if status is DeliveryStatus.SENT:
            values['sent_at'] = func.now()
        async with self._errors():
            await self.session.execute(
                update(DeliveryRow).where(DeliveryRow.idempotency_key == idempotency_key).values(**values),
            )
            await self.session.commit()

    async def release(self, idempotency_key: str, error: str) -> None:
        async with self._errors():
            await self.session.execute(
                update(DeliveryRow)
                .where(
                    DeliveryRow.idempotency_key == idempotency_key,
                    # Снимается только незавершённое: отправленное письмо из
                    # истории не исчезает, даже если повтор пришёл после успеха.
                    DeliveryRow.status == DeliveryStatus.PENDING.value,
                )
                .values(locked_until=None, error=error),
            )
            await self.session.commit()

    async def list_for_user(self, user_id: UUID, page_number: int, page_size: int) -> Page[Delivery]:
        base = select(DeliveryRow).where(DeliveryRow.user_id == user_id)
        async with self._errors():
            total = await self.session.scalar(
                select(func.count()).select_from(base.subquery()),
            )
            rows = (
                await self.session.scalars(
                    base.order_by(DeliveryRow.created_at.desc())
                    .offset((page_number - 1) * page_size)
                    .limit(page_size),
                )
            ).all()
        return Page[Delivery](
            items=[_delivery(row) for row in rows],
            total=total or 0,
            page_number=page_number,
            page_size=page_size,
        )


def _delivery(row: DeliveryRow) -> Delivery:
    return Delivery(
        id=row.id, idempotency_key=row.idempotency_key, user_id=row.user_id,
        channel=Channel(row.channel), template_code=row.template_code, subject=row.subject,
        status=DeliveryStatus(row.status), error=row.error, created_at=row.created_at, sent_at=row.sent_at,
    )


class PostgresCampaignRepository(PostgresRepository, CampaignRepository):
    async def create(self, draft: CampaignDraft, created_by: str | None) -> Campaign:
        status = CampaignStatus.SCHEDULED if (draft.scheduled_at or draft.cron) else CampaignStatus.RUNNING
        row = CampaignRow(
            title=draft.title, template_code=draft.template_code, channel=draft.channel.value,
            audience=draft.audience.model_dump(mode='json'), context=draft.context,
            status=status.value, scheduled_at=draft.scheduled_at, cron=draft.cron, created_by=created_by,
        )
        async with self._errors():
            self.session.add(row)
            await self.session.commit()
            await self.session.refresh(row)
        return _campaign(row)

    async def get(self, campaign_id: UUID) -> Campaign | None:
        async with self._errors():
            row = await self.session.get(CampaignRow, campaign_id)
        return _campaign(row) if row else None

    async def list_all(self) -> list[Campaign]:
        async with self._errors():
            rows = (await self.session.scalars(select(CampaignRow).order_by(CampaignRow.created_at.desc()))).all()
        return [_campaign(row) for row in rows]

    async def set_status(self, campaign_id: UUID, status: str) -> Campaign | None:
        async with self._errors():
            row = await self.session.scalar(
                update(CampaignRow).where(CampaignRow.id == campaign_id).values(status=status).returning(CampaignRow),
            )
            await self.session.commit()
        return _campaign(row) if row else None

    async def due(self, moment: datetime) -> list[Campaign]:
        query = select(CampaignRow).where(
            CampaignRow.status.in_((CampaignStatus.SCHEDULED.value, CampaignStatus.RUNNING.value)),
        )
        async with self._errors():
            rows = (await self.session.scalars(query)).all()
        # Разовые отбираются по времени здесь, повторяемые — по расписанию в
        # планировщике: разбирать cron в SQL нечем.
        return [
            _campaign(row) for row in rows
            if row.cron or row.scheduled_at is None or row.scheduled_at <= moment
        ]

    async def claim_run(
        self, campaign_id: UUID, period_key: str, event_id: UUID, publication: OutboxDraft, finish: bool,
    ) -> bool:
        query = (
            pg_insert(CampaignRunRow)
            .values(campaign_id=campaign_id, period_key=period_key, event_id=event_id)
            .on_conflict_do_nothing(index_elements=[CampaignRunRow.campaign_id, CampaignRunRow.period_key])
            .returning(CampaignRunRow.id)
        )
        async with self._errors():
            claimed = await self.session.scalar(query)
            if claimed is not None:
                # Отметка запуска, задание на публикацию и завершение разовой
                # рассылки — одной транзакцией: «запуск был, а событие не ушло»
                # больше не случается.
                self.session.add(_outbox_row(publication))
                if finish:
                    await self.session.execute(
                        update(CampaignRow)
                        .where(CampaignRow.id == campaign_id)
                        .values(status=CampaignStatus.DONE.value),
                    )
            await self.session.commit()
        return claimed is not None

    async def context_of(self, campaign_id: UUID) -> dict[str, Any]:
        async with self._errors():
            row = await self.session.get(CampaignRow, campaign_id)
        return dict(row.context) if row else {}


def _campaign(row: CampaignRow) -> Campaign:
    from models.event import Audience  # локальный импорт: иначе круг с моделями кампаний

    return Campaign(
        id=row.id, title=row.title, template_code=row.template_code, channel=Channel(row.channel),
        audience=Audience.model_validate(row.audience), status=CampaignStatus(row.status),
        scheduled_at=row.scheduled_at, cron=row.cron, created_by=row.created_by,
        created_at=row.created_at, updated_at=row.updated_at,
    )


class PostgresShortLinkRepository(PostgresRepository, ShortLinkRepository):
    async def create(
        self, target_url: str, user_id: UUID | None, expires_at: datetime | None, purpose: str | None,
    ) -> ShortLink:
        for _ in range(SHORT_KEY_ATTEMPTS):
            key = ''.join(secrets.choice(SHORT_KEY_ALPHABET) for _ in range(SHORT_KEY_LENGTH))
            query = (
                pg_insert(ShortLinkRow)
                .values(key=key, target_url=target_url, user_id=user_id, expires_at=expires_at, purpose=purpose)
                .on_conflict_do_nothing(index_elements=[ShortLinkRow.key])
                .returning(ShortLinkRow)
            )
            async with self._errors():
                row = await self.session.scalar(query)
                await self.session.commit()
            if row is not None:
                return ShortLink.model_validate(row)
        raise StorageUnavailableError('Не удалось подобрать свободный ключ короткой ссылки')

    async def resolve(self, key: str, at: datetime) -> ShortLink | None:
        # Переход считается тем же запросом, которым читается ссылка: отдельным
        # UPDATE счётчик разошёлся бы с числом ответов при сбое между ними.
        query = (
            update(ShortLinkRow)
            .where(ShortLinkRow.key == key)
            .values(visits=ShortLinkRow.visits + 1)
            .returning(ShortLinkRow)
        )
        async with self._errors():
            row = await self.session.scalar(query)
            await self.session.commit()
        if row is None:
            return None
        link = ShortLink.model_validate(row)
        # Просроченная ссылка — это 404 по заданию, а не редирект.
        if link.expires_at is not None and link.expires_at <= at:
            return None
        return link


class PostgresEmailConfirmationRepository(PostgresRepository, EmailConfirmationRepository):
    async def issue(self, token_hash: str, user_id: UUID, email: str, expires_at: datetime) -> None:
        async with self._errors():
            self.session.add(
                EmailConfirmationTokenRow(token_hash=token_hash, user_id=user_id, email=email, expires_at=expires_at),
            )
            await self.session.commit()

    async def confirm(self, token_hash: str, at: datetime) -> EmailConfirmation | None:
        # Токен гасится условным UPDATE: из двух одновременных переходов по
        # ссылке строку получит только один, второй увидит used_at и уйдёт ни
        # с чем. Проверка «не погашен ли» отдельным SELECT этого не даёт.
        spend = (
            update(EmailConfirmationTokenRow)
            .where(
                EmailConfirmationTokenRow.token_hash == token_hash,
                EmailConfirmationTokenRow.used_at.is_(None),
                EmailConfirmationTokenRow.expires_at > at,
            )
            .values(used_at=at)
            .returning(EmailConfirmationTokenRow.user_id, EmailConfirmationTokenRow.email)
        )
        async with self._errors():
            spent = (await self.session.execute(spend)).first()
            if spent is None:
                await self.session.rollback()
                return None
            user_id, email = spent
            mark = (
                pg_insert(EmailConfirmationRow)
                .values(user_id=user_id, email=email, confirmed_at=at)
                .on_conflict_do_update(
                    index_elements=[EmailConfirmationRow.user_id],
                    set_={'email': email, 'confirmed_at': at},
                )
            )
            await self.session.execute(mark)
            # Одна транзакция на погашение и отметку: сбой между ними не
            # оставит токен погашенным, а адрес неподтверждённым.
            await self.session.commit()
        return EmailConfirmation(user_id=user_id, email=email, confirmed_at=at)

    async def get(self, user_id: UUID) -> EmailConfirmation | None:
        async with self._errors():
            row = await self.session.get(EmailConfirmationRow, user_id)
        return EmailConfirmation.model_validate(row) if row else None


def new_id() -> UUID:
    """Идентификатор для записей, которые создаются вне ORM."""
    return uuid4()
