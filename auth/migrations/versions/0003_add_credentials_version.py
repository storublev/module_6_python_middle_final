"""Версия учётных данных пользователя.

Растёт при смене пароля в одной транзакции с ним. Сессия действует, только
пока её версия совпадает с версией пользователя: смена пароля закрывает
остальные сессии, даже если удалить их из Redis не удалось.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'auth'


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('credentials_version', sa.Integer(), server_default=sa.text('0'), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column('users', 'credentials_version', schema=SCHEMA)
