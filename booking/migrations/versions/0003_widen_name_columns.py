"""Колонки имён вмещают самое длинное имя, которое разрешает сервис авторизации.

Auth разрешает имя и фамилию по 64 символа, а имя для страниц и писем — это
«имя фамилия» через пробел, до 129 символов. Колонки были на 128, и зритель
с допустимым профилем получал ошибку базы при создании показа, брони или
оценки. Расширение varchar в PostgreSQL не переписывает таблицу.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'booking'
# Значение зафиксировано здесь, а не взято из кода: миграция описывает схему
# на момент своего создания и не должна меняться вместе с константой.
DISPLAY_NAME_MAX_LENGTH = 129
COLUMNS = (('screenings', 'host_name'), ('bookings', 'guest_name'), ('ratings', 'author_name'))


def upgrade() -> None:
    for table, column in COLUMNS:
        op.alter_column(
            table, column, schema=SCHEMA,
            type_=sa.String(DISPLAY_NAME_MAX_LENGTH), existing_type=sa.String(128), existing_nullable=False,
        )


def downgrade() -> None:
    for table, column in COLUMNS:
        op.alter_column(
            table, column, schema=SCHEMA,
            type_=sa.String(128), existing_type=sa.String(DISPLAY_NAME_MAX_LENGTH), existing_nullable=False,
            postgresql_using=f'left({column}, 128)',
        )
