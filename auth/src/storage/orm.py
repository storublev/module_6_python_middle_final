"""Таблицы PostgreSQL. По ним же Alembic сверяет миграции со схемой."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ARRAY, DateTime, ForeignKey, Index, MetaData, String, Text, false, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = 'auth'

# Имена ограничений задаются явно, чтобы миграции ссылались на них одинаково
# на любой базе, а не на имена, которые придумал PostgreSQL.
NAMING_CONVENTION = {
    'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserRow(Timestamped, Base):
    __tablename__ = 'users'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Растёт при смене пароля в той же транзакции. Сессия запоминает версию, с
    # которой открыта, и с устаревшей не действует — даже если удалить её из
    # Redis при смене пароля не удалось.
    credentials_version: Mapped[int] = mapped_column(server_default=text('0'))
    is_superuser: Mapped[bool] = mapped_column(server_default=false())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class RoleRow(Timestamped, Base):
    __tablename__ = 'roles'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # Права роли — короткий список строк, который всегда читается целиком
    # вместе с ролью: отдельная таблица дала бы лишний JOIN без выгоды.
    permissions: Mapped[list[str]] = mapped_column(ARRAY(String(128)), server_default=text("'{}'"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class UserRoleRow(Timestamped, Base):
    __tablename__ = 'user_roles'
    # Первичный ключ (user_id, role_id) ищет роли пользователя, этот индекс —
    # пользователей роли.
    __table_args__ = (Index(None, 'role_id'),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True)


class LoginHistoryRow(Timestamped, Base):
    __tablename__ = 'login_history'
    # История читается постранично, от новых входов к старым, по одному пользователю.
    __table_args__ = (Index(None, 'user_id', 'created_at'),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip: Mapped[str | None] = mapped_column(String(45))
