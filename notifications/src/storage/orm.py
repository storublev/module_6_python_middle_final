"""Таблицы PostgreSQL. По ним же Alembic сверяет миграции со схемой."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = 'notify'

# Имена ограничений задаются явно, чтобы миграции ссылались на них одинаково
# на любой базе, а не на имена, которые придумал PostgreSQL.
NAMING_CONVENTION = {
    'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}

# JSONB в PostgreSQL, обычный JSON в остальных базах: unit-тесты гоняют те же
# модели на SQLite, где JSONB нет.
Json = JSONB().with_variant(JSON(), 'sqlite')


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventRow(Timestamped, Base):
    """Принятое событие.

    Хранится ради одного — идемпотентности приёма: `event_id` задаёт
    отправитель, и повторный запрос после потерянного ответа натыкается на
    первичный ключ вместо того, чтобы создать второе уведомление.
    """

    __tablename__ = 'events'

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    routing_key: Mapped[str] = mapped_column(String(128))
    template_code: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(Json)


class TemplateRow(Timestamped, Base):
    """Действующая версия шаблона."""

    __tablename__ = 'templates'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    channel: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    # Растёт при каждой правке. В сообщение очереди едет пара (код, версия):
    # правка шаблона посреди рассылки не должна разослать половине зрителей
    # одно письмо, а половине другое.
    version: Mapped[int] = mapped_column(server_default=text('1'))
    is_active: Mapped[bool] = mapped_column(server_default=true())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class TemplateVersionRow(Timestamped, Base):
    """Прошлые версии шаблона: по ним досылаются начатые рассылки."""

    __tablename__ = 'template_versions'
    __table_args__ = (UniqueConstraint('code', 'version'),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    name: Mapped[str] = mapped_column(String(128))
    channel: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)


class SubscriptionRow(Base):
    """Явная настройка уведомлений зрителя.

    Отсутствие строки означает согласие: подписывать заново всех при
    добавлении нового типа уведомлений никто не станет, а вот отказ хранить
    обязательно.
    """

    __tablename__ = 'subscriptions'
    __table_args__ = (UniqueConstraint('user_id', 'template_code', 'channel'),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID]
    template_code: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(server_default=true())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class NotificationRow(Timestamped, Base):
    """О чём зрителю уже сообщали.

    `content_version` — версия данных последнего письма. Совпадение версий
    гасит событие: о восьмой серии нельзя писать дважды.
    """

    __tablename__ = 'notifications'
    __table_args__ = (UniqueConstraint('user_id', 'template_code', 'content_id'),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID]
    template_code: Mapped[str] = mapped_column(String(64))
    content_id: Mapped[str] = mapped_column(String(128))
    content_version: Mapped[int | None]
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeliveryRow(Timestamped, Base):
    """Факт отправки одного сообщения одному зрителю.

    `idempotency_key` уникален — это защита от дублей поверх гарантии
    at-least-once: повторно доставленное сообщение натыкается на занятый ключ
    и второго письма не порождает.
    """

    __tablename__ = 'deliveries'
    __table_args__ = (
        # Личный кабинет показывает последние уведомления зрителя: без индекса
        # это чтение обходило бы всю таблицу отправок.
        Index('ix_deliveries_user_id_created_at', 'user_id', 'created_at'),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    user_id: Mapped[UUID]
    channel: Mapped[str] = mapped_column(String(16))
    template_code: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignRow(Timestamped, Base):
    """Рассылка менеджера: разовая, отложенная или повторяемая."""

    __tablename__ = 'campaigns'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255))
    template_code: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16))
    audience: Mapped[dict[str, Any]] = mapped_column(Json)
    context: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(16))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cron: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class CampaignRunRow(Base):
    """Запуск рассылки за период.

    Уникальный ключ `(campaign_id, period_key)` — защита от повторов после
    простоя генератора: проснувшись через сутки, он видит, что запуск за
    прошлую пятницу уже был, и не рассылает её второй раз.
    """

    __tablename__ = 'campaign_runs'
    __table_args__ = (UniqueConstraint('campaign_id', 'period_key'),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey(f'{SCHEMA}.campaigns.id', ondelete='CASCADE'))
    period_key: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[UUID]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ShortLinkRow(Timestamped, Base):
    """Короткая ссылка из письма."""

    __tablename__ = 'short_links'

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_url: Mapped[str] = mapped_column(Text)
    # Кому выдана: по этому полю считаются переходы и подтверждается адрес.
    user_id: Mapped[UUID | None]
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    visits: Mapped[int] = mapped_column(server_default=text('0'))
    purpose: Mapped[str | None] = mapped_column(String(32))
