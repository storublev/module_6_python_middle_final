"""Роль subscribers с правом смотреть фильмы по подписке.

Фильмы, вышедшие менее трёх лет назад, ETL помечает access_level=subscription;
смотреть их может обладатель права films.subscription.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = 'subscribers'


def upgrade() -> None:
    op.execute(
        sa.text(
            'INSERT INTO auth.roles (id, name, description, permissions) '
            'VALUES (gen_random_uuid(), :name, :description, :permissions) '
            'ON CONFLICT (name) DO NOTHING',
        ).bindparams(
            sa.bindparam('permissions', type_=sa.ARRAY(sa.String())),
            name=ROLE_NAME,
            description='Подписчики: доступны фильмы, вышедшие менее трёх лет назад',
            permissions=['films.subscription'],
        ),
    )


def downgrade() -> None:
    op.execute(sa.text('DELETE FROM auth.roles WHERE name = :name').bindparams(name=ROLE_NAME))
