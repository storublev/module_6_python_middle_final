"""Связанные аккаунты соцсетей и учётные записи без пароля.

Вход через соцсеть заводит учётную запись, в которую нельзя войти по паролю:
пароля у неё нет, пока владелец сам его не задаст. Поэтому password_hash
становится необязательным.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('users', 'password_hash', existing_type=sa.String(255), nullable=True, schema='auth')
    op.create_table(
        'social_accounts',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('social_id', sa.String(length=128), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'], name='fk_social_accounts_user_id_users', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_social_accounts'),
        # Один аккаунт соцсети принадлежит одной учётной записи кинотеатра.
        sa.UniqueConstraint('provider', 'social_id', name='uq_social_accounts_provider_social_id'),
        # И не больше одного аккаунта каждой соцсети у пользователя: личный
        # кабинет и открепление работают по имени поставщика.
        sa.UniqueConstraint('user_id', 'provider', name='uq_social_accounts_user_id_provider'),
        schema='auth',
    )


def downgrade() -> None:
    op.drop_table('social_accounts', schema='auth')
    # Учётным записям без пароля он нужен, иначе колонку не сделать
    # обязательной: ставим заведомо непригодный хеш — войти им нельзя.
    op.execute(sa.text("UPDATE auth.users SET password_hash = '!' WHERE password_hash IS NULL"))
    op.alter_column('users', 'password_hash', existing_type=sa.String(255), nullable=False, schema='auth')
