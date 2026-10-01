"""Outbox: задания на публикацию в RabbitMQ пишутся в базу вместе с данными.

До этой миграции API сначала запоминал событие, а потом публиковал его в
RabbitMQ. Если брокер лежал, событие оставалось в базе, а в очередь не
попадало, и повтор того же запроса получал `accepted: false` — письмо
терялось насовсем. С рассылками было так же: запуск за период отмечался до
публикации и больше не повторялся.

Теперь событие и задание на публикацию записываются одной транзакцией, а
переносит задания в RabbitMQ отдельный процесс-ретранслятор (`relay.py`),
повторяя попытку, пока брокер не примет.

`available_at` — момент, с которого задание можно брать. Ретранслятор,
забрав задание, сдвигает его вперёд на срок аренды: упади он посреди
публикации, задание вернётся к другому экземпляру, когда срок выйдет.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'notify'


def upgrade() -> None:
    op.create_table(
        'outbox',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('stage', sa.String(16), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('request_id', sa.String(128), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_outbox'),
        schema=SCHEMA,
    )
    # Ретранслятор каждые полсекунды ищет задания, которым пора: без индекса
    # это был бы обход всей таблицы.
    op.create_index('ix_outbox_available_at', 'outbox', ['available_at'], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index('ix_outbox_available_at', table_name='outbox', schema=SCHEMA)
    op.drop_table('outbox', schema=SCHEMA)
