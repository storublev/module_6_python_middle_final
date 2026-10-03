"""Ретранслятор outbox: отдельный процесс, переносящий события о бронях в сервис уведомлений.

API бронирования в сервис уведомлений не ходит: события ложатся в базу той же
транзакцией, что и бронь. Этот процесс забирает их, отправляет и удаляет.
Сервис уведомлений лежит — события ждут в базе и уходят, как только он
поднимется (НФТ-4).

Экземпляров можно запустить несколько: события разбираются через
`FOR UPDATE SKIP LOCKED`.
"""

import asyncio
import logging
import signal
from logging.config import dictConfig

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import db.postgres as postgres
from core.config import settings
from core.logger import LOGGING
from core.sentry import configure_sentry
from services.relay import OutboxRelay
from storage.base import NotificationGateway, StorageUnavailableError
from storage.http import HttpNotifications
from storage.postgres import PostgresOutbox

dictConfig(LOGGING)
logger = logging.getLogger(__name__)


async def run() -> None:
    configure_sentry(settings.sentry_dsn, f'{settings.project_name}-relay', settings.sentry_environment)
    postgres.engine = postgres.create_engine(settings)
    sessions = async_sessionmaker(postgres.engine, expire_on_commit=False)
    client = httpx.AsyncClient(base_url=settings.notify_url, timeout=settings.notify_timeout)
    gateway = HttpNotifications(client, settings.service_token.get_secret_value())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info('Ретранслятор событий бронирования запущен')
    while not stop.is_set():
        try:
            sent, full = await tick(sessions, gateway)
        except StorageUnavailableError as error:
            # База прилегла: события никуда не денутся, ждём следующего шага.
            logger.warning('Шаг ретранслятора пропущен: %s', error)
            sent, full = 0, False
        except Exception:
            logger.exception('Шаг ретранслятора завершился ошибкой')
            sent, full = 0, False
        if sent:
            logger.info('Отправлено событий: %s', sent)
        if full:
            # Пачка набралась целиком — в базе, скорее всего, ещё есть.
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.outbox_poll_interval)
        except TimeoutError:
            continue

    await client.aclose()
    if postgres.engine is not None:
        await postgres.engine.dispose()
    logger.info('Ретранслятор остановлен')


async def tick(sessions: async_sessionmaker[AsyncSession], gateway: NotificationGateway) -> tuple[int, bool]:
    """Один проход. Возвращает число отправленных и признак полной пачки."""
    async with sessions() as session:
        relay = OutboxRelay(
            PostgresOutbox(session), gateway, settings.outbox_batch_size, settings.outbox_lease, settings.retry_delay,
        )
        sent = await relay.relay_once()
    return sent, sent >= settings.outbox_batch_size


if __name__ == '__main__':
    asyncio.run(run())
