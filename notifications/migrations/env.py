"""Запуск миграций Alembic на асинхронном движке сервиса."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import settings
from storage.orm import SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
VERSION_TABLE = 'alembic_version'


def include_name(name: str | None, type_: str, _: dict) -> bool:
    """Что автогенерация сравнивает с моделями: только схема сервиса."""
    if type_ == 'schema':
        return name == SCHEMA
    if type_ != 'table':
        return True
    return name != VERSION_TABLE


def run_migrations(connection: Connection) -> None:
    # Таблица версий Alembic живёт в схеме сервиса, а не в public.
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}'))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_name=include_name,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Схема по умолчанию — public, даже если пользователь БД называется как
    # схема сервиса: иначе PostgreSQL сделает схемой по умолчанию её, и
    # автогенерация перестанет узнавать таблицы с явно заданной схемой. Те же
    # грабли уже ловили в сервисе авторизации.
    engine = create_async_engine(
        settings.postgres_dsn,
        connect_args={'server_settings': {'search_path': 'public'}},
    )
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
        await connection.commit()
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.postgres_dsn,
        target_metadata=target_metadata,
        version_table_schema=SCHEMA,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
