"""Аренда отправки вместо вечной брони.

До этой миграции отправитель вставлял запись `PENDING` и только потом
отправлял письмо, а повтор считал **любую** запись с тем же ключом признаком
того, что письмо уже ушло. Отправитель, упавший между этими шагами, оставлял
запись навсегда: после перезапуска письмо пропускалось, а сообщение
подтверждалось брокеру.

Теперь у записи есть срок аренды. Пока он идёт, письмо отправляет его
держатель; когда вышел — запись может забрать другой отправитель, и ровно
один. Записи `PENDING`, оставшиеся от прежней схемы, получают пустую аренду —
то есть сразу доступны повтору: это и есть письма, застрявшие из-за прежней
ошибки.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'notify'


def upgrade() -> None:
    op.add_column('deliveries', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.add_column(
        'deliveries', sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False), schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column('deliveries', 'attempts', schema=SCHEMA)
    op.drop_column('deliveries', 'locked_until', schema=SCHEMA)
