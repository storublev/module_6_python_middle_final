"""Таблицы PostgreSQL. По ним же Alembic сверяет миграции со схемой.

Инварианты брони записаны ограничениями базы, а не только проверками в коде:
код может ошибиться, база — нет.

* `ck_screenings_seats` — мест занято не больше, чем есть (ФТ-12);
* `uq_bookings_active_guest` — у гостя одна активная бронь на показ (ФТ-14);
* `uq_ratings_pair` — одна оценка на пару за показ (ФТ-18).
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = 'booking'

# Имена ограничений задаются явно, чтобы миграции ссылались на них одинаково
# на любой базе, а не на имена, которые придумал PostgreSQL.
NAMING_CONVENTION = {
    'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class ScreeningRow(Timestamped, Base):
    """Показ: хост предлагает фильм, место и время."""

    __tablename__ = 'screenings'
    __table_args__ = (
        CheckConstraint('seats_taken >= 0 AND seats_taken <= capacity', name='seats'),
        CheckConstraint('capacity > 0', name='capacity'),
        # Карточка фильма: будущие показы фильма по времени — самый частый
        # запрос сервиса (300 в секунду в пике). Отменённые в него не входят,
        # поэтому индекс частичный и меньше.
        Index(
            'ix_screenings_film_upcoming', 'film_id', 'starts_at',
            postgresql_where=text("status = 'scheduled'"),
        ),
        # Расписание хоста — все его показы, включая отменённые.
        Index('ix_screenings_host_starts', 'host_id', 'starts_at'),
        Index('ix_screenings_upcoming', 'starts_at', postgresql_where=text("status = 'scheduled'")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    host_id: Mapped[UUID]
    host_name: Mapped[str] = mapped_column(String(128))
    film_id: Mapped[UUID]
    film_title: Mapped[str] = mapped_column(String(255))
    film_poster: Mapped[str | None] = mapped_column(String(512))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    place: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    capacity: Mapped[int] = mapped_column(SmallInteger)
    seats_taken: Mapped[int] = mapped_column(SmallInteger, server_default=text('0'))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'scheduled'"))


class BookingRow(Timestamped, Base):
    """Бронь гостя: сколько мест он занял на показе."""

    __tablename__ = 'bookings'
    __table_args__ = (
        CheckConstraint('seats > 0', name='seats'),
        # Одна активная бронь гостя на показ. Индекс частичный: отменённая
        # бронь не мешает забронировать заново.
        Index(
            'uq_bookings_active_guest', 'screening_id', 'guest_id',
            unique=True, postgresql_where=text("status = 'active'"),
        ),
        # Страница «Мои брони».
        Index('ix_bookings_guest_created', 'guest_id', 'created_at'),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    screening_id: Mapped[UUID] = mapped_column(ForeignKey('screenings.id', ondelete='CASCADE'))
    guest_id: Mapped[UUID]
    guest_name: Mapped[str] = mapped_column(String(128))
    seats: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))


class RatingRow(Base):
    """Оценка участника показа."""

    __tablename__ = 'ratings'
    __table_args__ = (
        UniqueConstraint('screening_id', 'author_id', 'target_id', name='uq_ratings_pair'),
        CheckConstraint('score BETWEEN 1 AND 5', name='score'),
        # Отзывы о зрителе в одной роли, новые сверху.
        Index('ix_ratings_target', 'target_id', 'target_role', 'created_at'),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    screening_id: Mapped[UUID] = mapped_column(ForeignKey('screenings.id', ondelete='CASCADE'))
    author_id: Mapped[UUID]
    author_name: Mapped[str] = mapped_column(String(128))
    target_id: Mapped[UUID]
    target_role: Mapped[str] = mapped_column(String(16))
    score: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserRatingRow(Base):
    """Готовый агрегат оценок зрителя в одной роли (ADR-26)."""

    __tablename__ = 'user_ratings'

    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    score_sum: Mapped[int] = mapped_column(server_default=text('0'))
    votes: Mapped[int] = mapped_column(server_default=text('0'))


class OutboxRow(Base):
    """Событие для сервиса уведомлений, ожидающее отправки (ADR-23).

    `available_at` — когда событие можно забрать: при захвате ретранслятор
    сдвигает его вперёд на время аренды, после неудачи — на паузу повтора.
    """

    __tablename__ = 'outbox'
    __table_args__ = (Index('ix_outbox_available_at', 'available_at'),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_id: Mapped[str] = mapped_column(String(64), server_default=text("'-'"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(server_default=text('0'))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
