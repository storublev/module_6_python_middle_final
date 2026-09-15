"""Задания на сброс кеша прав.

Задание пишется в одной транзакции с назначением, отзывом, изменением или
удалением роли и удаляется, когда кеш в Redis сброшен. Если Redis был
недоступен, задание выполняет фоновый повтор.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'auth'


def upgrade() -> None:
    op.create_table(
        'access_invalidations',
        sa.Column('id', sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_access_invalidations')),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table('access_invalidations', schema=SCHEMA)
