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
    """Сессия SQLAlchemy на один запрос."""
    async with session_factory() as session:
        yield session
