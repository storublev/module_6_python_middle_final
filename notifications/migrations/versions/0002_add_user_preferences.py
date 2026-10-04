"""Общий отказ от уведомлений — свойство зрителя, а не одной подписки.

До этой таблицы «отписаться от всего» означало «выключить уже заданные
настройки». У зрителя, который никогда ничего не настраивал, таких записей
нет — и отписка по ссылке из письма не делала ничего, а письма продолжали
приходить. Поймано функциональным тестом, который проходит по ссылке **из
письма**, а не дёргает эндпоинт напрямую.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'notify'


def upgrade() -> None:
    op.create_table(
        'user_preferences',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('unsubscribed_all', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('user_id', name='pk_user_preferences'),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table('user_preferences', schema=SCHEMA)
