"""История входов — секционированная таблица (RANGE по месяцам).

По заданию история растёт до миллионов записей. Почему выбран месяц и почему
не устройство и не пользователь — в `storage/partitions.py`.

Обычную таблицу превратить в секционированную на месте нельзя, поэтому старая
переименовывается, рядом создаётся секционированная, данные переносятся, а
старая удаляется. На больших объёмах это делают в несколько приёмов и с
`ATTACH PARTITION`, здесь же таблица маленькая и переносится одним запросом.

Секции создаются на текущий месяц и на PREPARED_MONTHS вперёд, плюс секция по
умолчанию: без неё вход в неучтённый месяц завершился бы ошибкой. Дальше
секции создаёт `python cli.py create-login-partitions`.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-16
"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'auth'
TABLE = 'login_history'
OLD_TABLE = 'login_history_unpartitioned'
# Сколько месяцев вперёд подготовить сразу: хватит, чтобы обслуживание не
# горело, даже если про команду создания секций забудут на квартал.
PREPARED_MONTHS = 6


def month_start(day: date) -> date:
    return day.replace(day=1)


def next_month(month: date) -> date:
    if month.month == 12:
        return month.replace(year=month.year + 1, month=1, day=1)
    return month.replace(month=month.month + 1, day=1)


def rename_old_constraints() -> None:
    """Освобождает имена ограничений старой таблицы.

    ALTER TABLE ... RENAME переименовывает только таблицу: её первичный ключ,
    внешний ключ и индекс сохраняют прежние имена и заняли бы имена новой.
    """
    op.execute(sa.text(
        f'ALTER TABLE {SCHEMA}.{OLD_TABLE} RENAME CONSTRAINT pk_{TABLE} TO pk_{OLD_TABLE}',
    ))
    op.execute(sa.text(
        f'ALTER TABLE {SCHEMA}.{OLD_TABLE} '
        f'RENAME CONSTRAINT fk_{TABLE}_user_id_users TO fk_{OLD_TABLE}_user_id_users',
    ))
    op.execute(sa.text(
        f'ALTER INDEX {SCHEMA}.ix_{TABLE}_user_id_created_at RENAME TO ix_{OLD_TABLE}_user_id_created_at',
    ))


def upgrade() -> None:
    op.rename_table(TABLE, OLD_TABLE, schema=SCHEMA)
    rename_old_constraints()
    op.execute(
        sa.text(f"""
            CREATE TABLE {SCHEMA}.{TABLE} (
                id uuid NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                user_id uuid NOT NULL,
                user_agent varchar(512),
                ip varchar(45),
                CONSTRAINT pk_{TABLE} PRIMARY KEY (id, created_at),
                CONSTRAINT fk_{TABLE}_user_id_users FOREIGN KEY (user_id)
                    REFERENCES {SCHEMA}.users (id) ON DELETE CASCADE
            ) PARTITION BY RANGE (created_at)
        """),
    )
    # Индекс на родителе: PostgreSQL заводит такой же в каждой секции, включая
    # созданные позже, — про индексы новых секций можно не помнить.
    op.create_index(f'ix_{TABLE}_user_id_created_at', TABLE, ['user_id', 'created_at'], schema=SCHEMA)

    month = month_start(date.today())
    for _ in range(PREPARED_MONTHS):
        create_partition(month)
        month = next_month(month)
    op.execute(
        sa.text(f'CREATE TABLE {SCHEMA}.{TABLE}_default PARTITION OF {SCHEMA}.{TABLE} DEFAULT'),
    )

    op.execute(
        sa.text(f"""
            INSERT INTO {SCHEMA}.{TABLE} (id, created_at, user_id, user_agent, ip)
            SELECT id, created_at, user_id, user_agent, ip FROM {SCHEMA}.{OLD_TABLE}
        """),
    )
    op.drop_table(OLD_TABLE, schema=SCHEMA)


def create_partition(month: date) -> None:
    name = f'{TABLE}_y{month.year:04d}m{month.month:02d}'
    op.execute(
        sa.text(
            f'CREATE TABLE {SCHEMA}.{name} PARTITION OF {SCHEMA}.{TABLE} '
            f"FOR VALUES FROM ('{month}') TO ('{next_month(month)}')",
        ),
    )


def downgrade() -> None:
    op.rename_table(TABLE, OLD_TABLE, schema=SCHEMA)
    rename_old_constraints()
    op.create_table(
        TABLE,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('ip', sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(
            ['user_id'], [f'{SCHEMA}.users.id'], name=f'fk_{TABLE}_user_id_users', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=f'pk_{TABLE}'),
        schema=SCHEMA,
    )
    op.create_index(f'ix_{TABLE}_user_id_created_at', TABLE, ['user_id', 'created_at'], schema=SCHEMA)
    op.execute(
        sa.text(f"""
            INSERT INTO {SCHEMA}.{TABLE} (id, created_at, user_id, user_agent, ip)
            SELECT id, created_at, user_id, user_agent, ip FROM {SCHEMA}.{OLD_TABLE}
        """),
    )
    # Секции удаляются вместе с секционированной таблицей.
    op.execute(sa.text(f'DROP TABLE {SCHEMA}.{OLD_TABLE}'))
