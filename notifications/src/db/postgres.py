"""Подключение к PostgreSQL.

Тот же приём, что в сервисе авторизации: движок и фабрика сессий создаются в
lifespan приложения, а не при импорте, — иначе модуль нельзя импортировать без
работающей базы, и unit-тесты потянули бы её за собой.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from core.config import Settings

engine: AsyncEngine | None = None
session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.postgres_dsn,
        echo=settings.postgres_echo,
        # Проверять соединение из пула перед выдачей: после перезапуска
        # PostgreSQL в пуле остаются оборванные соединения.
        pool_pre_ping=True,
        connect_args={'timeout': 5},
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """Сессия SQLAlchemy на один запрос.

    Raises:
        RuntimeError: фабрика не создана — приложение не прошло lifespan.
    """
    if session_factory is None:
        raise RuntimeError('Фабрика сессий PostgreSQL не создана')
    async with session_factory() as session:
        yield session
