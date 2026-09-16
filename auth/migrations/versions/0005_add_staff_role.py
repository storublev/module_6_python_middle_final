"""Роль staff с правом входа в админку.

Сотрудники входят в админку каталога своей учётной записью кинотеатра: её
бэкенд аутентификации обменивает логин и пароль на токен в этом сервисе и
пускает только обладателя права admin.access.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = 'staff'


def upgrade() -> None:
    op.execute(
        sa.text(
            'INSERT INTO auth.roles (id, name, description, permissions) '
            'VALUES (gen_random_uuid(), :name, :description, :permissions) '
            'ON CONFLICT (name) DO NOTHING',
        ).bindparams(
            sa.bindparam('permissions', type_=sa.ARRAY(sa.String())),
            name=ROLE_NAME,
            description='Сотрудники: вход в админку каталога',
            permissions=['admin.access'],
        ),
    )


def downgrade() -> None:
    op.execute(sa.text('DELETE FROM auth.roles WHERE name = :name').bindparams(name=ROLE_NAME))
