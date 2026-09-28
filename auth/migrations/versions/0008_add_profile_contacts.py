"""Контакты и имя пользователя: почта, имя, фамилия и часовой пояс.

Нужны сервису уведомлений: письмо нельзя собрать, зная только логин, а
рассылать по московскому времени всей стране — значит будить Владивосток.
Все колонки необязательные: у существующих учётных записей их нет, и
заполняются они постепенно — из личного кабинета или входом через соцсеть.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'auth'
COLUMNS = (
    sa.Column('email', sa.String(254), nullable=True),
    sa.Column('first_name', sa.String(64), nullable=True),
    sa.Column('last_name', sa.String(64), nullable=True),
    sa.Column('timezone', sa.String(64), nullable=True),
)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column('users', column, schema=SCHEMA)
    # Рассылка «всем пользователям» идёт постранично по возрастанию id, и
    # адресаты без почты ей не нужны. Частичный индекс делает обход дешёвым:
    # без него каждая страница читала бы всю таблицу.
    op.create_index(
        'ix_users_id_with_email',
        'users',
        ['id'],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text('email IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_users_id_with_email', table_name='users', schema=SCHEMA)
    for column in reversed(COLUMNS):
        op.drop_column('users', column.name, schema=SCHEMA)
