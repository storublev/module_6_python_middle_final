"""Пользователи, роли, назначения ролей и история входов.

Revision ID: 0001
Revises:
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'auth'


def timestamp(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False)


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('login', sa.String(length=64), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('is_superuser', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        timestamp('updated_at'),
        timestamp('created_at'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
        sa.UniqueConstraint('login', name=op.f('uq_users_login')),
        schema=SCHEMA,
    )
    op.create_table(
        'roles',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('permissions', sa.ARRAY(sa.String(length=128)), server_default=sa.text("'{}'"), nullable=False),
        timestamp('updated_at'),
        timestamp('created_at'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_roles')),
        sa.UniqueConstraint('name', name=op.f('uq_roles_name')),
        schema=SCHEMA,
    )
    op.create_table(
        'user_roles',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('role_id', sa.Uuid(), nullable=False),
        timestamp('created_at'),
        sa.ForeignKeyConstraint(
            ['user_id'], [f'{SCHEMA}.users.id'], name=op.f('fk_user_roles_user_id_users'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['role_id'], [f'{SCHEMA}.roles.id'], name=op.f('fk_user_roles_role_id_roles'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('user_id', 'role_id', name=op.f('pk_user_roles')),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_user_roles_role_id'), 'user_roles', ['role_id'], schema=SCHEMA)
    op.create_table(
        'login_history',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('ip', sa.String(length=45), nullable=True),
        timestamp('created_at'),
        sa.ForeignKeyConstraint(
            ['user_id'], [f'{SCHEMA}.users.id'], name=op.f('fk_login_history_user_id_users'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_login_history')),
        schema=SCHEMA,
    )
    op.create_index(
        op.f('ix_login_history_user_id_created_at'), 'login_history', ['user_id', 'created_at'], schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table('login_history', schema=SCHEMA)
    op.drop_table('user_roles', schema=SCHEMA)
    op.drop_table('roles', schema=SCHEMA)
    op.drop_table('users', schema=SCHEMA)
