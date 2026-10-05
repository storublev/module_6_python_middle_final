"""Отклонённые события outbox остаются в базе с причиной отказа.

Раньше событие, которое сервис уведомлений отверг по существу (4xx), удалялось,
и после исправления ошибки письмо о брони или отмене восстановить было не из
чего. Теперь у события ставится `rejected_at`: ретранслятор его не берёт, а
`python outbox_cli.py requeue` возвращает его в отправку с тем же event_id.

Индекс выборки ретранслятора становится частичным — по ожидающим отправки
событиям; отклонённые лежат в своём, маленьком.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'booking'


def upgrade() -> None:
    op.add_column('outbox', sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.drop_index('ix_outbox_available_at', table_name='outbox', schema=SCHEMA)
    op.create_index(
        'ix_outbox_pending', 'outbox', ['available_at'], schema=SCHEMA,
        postgresql_where=sa.text('rejected_at IS NULL'),
    )
    op.create_index(
        'ix_outbox_rejected', 'outbox', ['rejected_at'], schema=SCHEMA,
        postgresql_where=sa.text('rejected_at IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_outbox_rejected', table_name='outbox', schema=SCHEMA)
    op.drop_index('ix_outbox_pending', table_name='outbox', schema=SCHEMA)
    op.create_index('ix_outbox_available_at', 'outbox', ['available_at'], schema=SCHEMA)
    op.drop_column('outbox', 'rejected_at', schema=SCHEMA)
