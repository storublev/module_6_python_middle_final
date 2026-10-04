"""Ретранслятор outbox: отдельный процесс, переносящий задания из базы в RabbitMQ.

API и генератор рассылок в брокер не пишут: они кладут задание в базу той же
транзакцией, что и данные. Этот процесс забирает задания, публикует их с
подтверждением и удаляет. Брокер лежит — задания ждут в базе и уходят, как
только он поднимется.

Экземпляров можно запустить несколько: задания разбираются через
`FOR UPDATE SKIP LOCKED`, и один и тот же никто не возьмёт дважды, пока идёт
его аренда.
"""

import asyncio
import logging
import signal
from logging.config import dictConfig

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import db.postgres as postgres
from core.config import settings
from core.logger import LOGGING
from core.sentry import configure_sentry
from services.relay import OutboxRelay
from storage.base import MessagePublisher, StorageUnavailableError
from storage.postgres import PostgresOutbox
from storage.rabbit import RabbitPublisher, connect

dictConfig(LOGGING)
logger = logging.getLogger(__name__)


async def run() -> None:
    configure_sentry(settings.sentry_dsn, f'{settings.project_name}-relay', settings.sentry_environment)
    postgres.engine = postgres.create_engine(settings)
    sessions = async_sessionmaker(postgres.engine, expire_on_commit=False)
    connection, channel = await connect(
        settings.rabbit_url.get_secret_value(),
        settings.rabbit_prefetch,
        int(settings.retry_delay.total_seconds() * 1000),
    )
    publisher = RabbitPublisher(channel, settings.rabbit_publish_timeout)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info('Ретранслятор outbox запущен')
    while not stop.is_set():
        try:
            published, full = await tick(sessions, publisher)
        except StorageUnavailableError as error:
            # База прилегла: задания никуда не денутся, ждём следующего шага.
            logger.warning('Шаг ретранслятора пропущен: %s', error)
            published, full = 0, False
        except Exception:
            logger.exception('Шаг ретранслятора завершился ошибкой')
            published, full = 0, False
        if published:
            logger.info('Опубликовано заданий: %s', published)
        if full:
            # Пачка набралась целиком — в базе, скорее всего, ещё есть: берём
            # следующую сразу, а не через паузу.
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.outbox_poll_interval)
        except asyncio.TimeoutError:
            continue

    await connection.close()
    if postgres.engine is not None:
        await postgres.engine.dispose()
    logger.info('Ретранслятор outbox остановлен')


async def tick(sessions: async_sessionmaker[AsyncSession], publisher: MessagePublisher) -> tuple[int, bool]:
    """Один проход. Возвращает число опубликованных и признак полной пачки."""
    async with sessions() as session:
        relay = OutboxRelay(
            PostgresOutbox(session),
            publisher,
            settings.outbox_batch_size,
            settings.outbox_lease,
            settings.retry_delay,
        )
        published = await relay.relay_once()
    return published, published >= settings.outbox_batch_size


if __name__ == '__main__':
    asyncio.run(run())
