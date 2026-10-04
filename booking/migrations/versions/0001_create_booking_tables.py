"""Таблицы сервиса бронирования: показы, брони, оценки, агрегаты оценок, outbox.

Инварианты брони заданы ограничениями базы: `ck_screenings_seats` не даёт
занять мест больше, чем есть, частичный уникальный индекс — завести гостю
вторую активную бронь, `uq_ratings_pair` — оценить одного участника дважды.

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'booking'


def timestamps() -> list[sa.Column]:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}')

    op.create_table(
        'screenings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('host_id', sa.Uuid(), nullable=False),
        sa.Column('host_name', sa.String(128), nullable=False),
        sa.Column('film_id', sa.Uuid(), nullable=False),
        sa.Column('film_title', sa.String(255), nullable=False),
        sa.Column('film_poster', sa.String(512), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('place', sa.String(255), nullable=False),
        sa.Column('address', sa.String(512), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('capacity', sa.SmallInteger(), nullable=False),
        sa.Column('seats_taken', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
        sa.Column('status', sa.String(16), server_default=sa.text("'scheduled'"), nullable=False),
        *timestamps(),
        sa.CheckConstraint('seats_taken >= 0 AND seats_taken <= capacity', name='ck_screenings_seats'),
        sa.CheckConstraint('capacity > 0', name='ck_screenings_capacity'),
        sa.PrimaryKeyConstraint('id', name='pk_screenings'),
        schema=SCHEMA,
    )
    op.create_index(
        'ix_screenings_film_upcoming', 'screenings', ['film_id', 'starts_at'],
        schema=SCHEMA, postgresql_where=sa.text("status = 'scheduled'"),
    )
    op.create_index('ix_screenings_host_starts', 'screenings', ['host_id', 'starts_at'], schema=SCHEMA)
    op.create_index(
        'ix_screenings_upcoming', 'screenings', ['starts_at'],
        schema=SCHEMA, postgresql_where=sa.text("status = 'scheduled'"),
    )

    op.create_table(
        'bookings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('screening_id', sa.Uuid(), nullable=False),
        sa.Column('guest_id', sa.Uuid(), nullable=False),
        sa.Column('guest_name', sa.String(128), nullable=False),
        sa.Column('seats', sa.SmallInteger(), nullable=False),
        sa.Column('status', sa.String(16), server_default=sa.text("'active'"), nullable=False),
        *timestamps(),
        sa.CheckConstraint('seats > 0', name='ck_bookings_seats'),
        sa.ForeignKeyConstraint(
            ['screening_id'], [f'{SCHEMA}.screenings.id'],
            name='fk_bookings_screening_id_screenings', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_bookings'),
        schema=SCHEMA,
    )
    op.create_index(
        'uq_bookings_active_guest', 'bookings', ['screening_id', 'guest_id'],
        unique=True, schema=SCHEMA, postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index('ix_bookings_guest_created', 'bookings', ['guest_id', 'created_at'], schema=SCHEMA)

    op.create_table(
        'ratings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('screening_id', sa.Uuid(), nullable=False),
        sa.Column('author_id', sa.Uuid(), nullable=False),
        sa.Column('author_name', sa.String(128), nullable=False),
        sa.Column('target_id', sa.Uuid(), nullable=False),
        sa.Column('target_role', sa.String(16), nullable=False),
        sa.Column('score', sa.SmallInteger(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('score BETWEEN 1 AND 5', name='ck_ratings_score'),
        sa.ForeignKeyConstraint(
            ['screening_id'], [f'{SCHEMA}.screenings.id'],
            name='fk_ratings_screening_id_screenings', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_ratings'),
        sa.UniqueConstraint('screening_id', 'author_id', 'target_id', name='uq_ratings_pair'),
        schema=SCHEMA,
    )
    op.create_index('ix_ratings_target', 'ratings', ['target_id', 'target_role', 'created_at'], schema=SCHEMA)

    op.create_table(
        'user_ratings',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('score_sum', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('votes', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'role', name='pk_user_ratings'),
        schema=SCHEMA,
    )

    op.create_table(
        'outbox',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('request_id', sa.String(64), server_default=sa.text("'-'"), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_outbox'),
        schema=SCHEMA,
    )
    op.create_index('ix_outbox_available_at', 'outbox', ['available_at'], schema=SCHEMA)


def downgrade() -> None:
    for table in ('outbox', 'user_ratings', 'ratings', 'bookings', 'screenings'):
        op.drop_table(table, schema=SCHEMA)
