"""Воркеры конвейера: планировщик, сборщик и отправитель.

Один образ, три роли — `NOTIFY_WORKER_ROLE=plan|render|send`. Роль выбирается
при запуске контейнера, и каждая масштабируется отдельно: у сборщика узкое
место — чужие сервисы, у отправителя — почтовый сервер, и связывать их одной
судьбой незачем (ADR-10).

Урок допускает и один воркер («формирует сообщение и, если он один,
отправляет»), и разделение. Мы разделили: так отказ почтового сервера не
мешает собирать письма впрок, а отказ сервиса авторизации не мешает отправлять
уже собранные.
"""

import asyncio
import logging
import os
import signal
import sys
from logging.config import dictConfig
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import db.postgres as postgres
from channels.base import DeliveryChannel
from channels.email import EmailChannel, LoggingChannel, RateLimiter, SmtpConnectionPool
from channels.websocket import WebsocketChannel
from core.config import settings
from core.logger import LOGGING
from core.request_id import set_request_id
from core.sentry import configure_sentry
from models.enums import Channel
from services.assembly import AssemblyService, QuietHours
from services.messages import PlanMessage, RenderMessage, SendMessage
from services.planner import PlannerService
from services.renderer import Renderer
from services.sender import SenderService
from storage.auth import AuthContactDirectory
from storage.postgres import (
    PostgresDeliveryRepository,
    PostgresNotificationRepository,
    PostgresSubscriptionRepository,
    PostgresTemplateRepository,
)
from storage.rabbit import STAGE_PLAN, STAGE_RENDER, STAGE_SEND, RabbitConsumer, RabbitPublisher, connect

dictConfig(LOGGING)
logger = logging.getLogger(__name__)

ROLES = (STAGE_PLAN, STAGE_RENDER, STAGE_SEND)


async def run(role: str) -> None:
    """Поднимает воркер выбранной роли и читает свою очередь, пока жив."""
    configure_sentry(settings.sentry_dsn, f'{settings.project_name}-{role}', settings.sentry_environment)
    postgres.engine = postgres.create_engine(settings)
    sessions = async_sessionmaker(postgres.engine, expire_on_commit=False)
    connection, channel = await connect(
        settings.rabbit_url.get_secret_value(),
        settings.rabbit_prefetch,
        int(settings.retry_delay.total_seconds() * 1000),
    )
    publisher = RabbitPublisher(channel)
    auth_client = httpx.AsyncClient(base_url=settings.auth_url, timeout=settings.auth_timeout)
    channels = build_channels()

    handler = build_handler(role, sessions, publisher, auth_client, channels)
    consumer = RabbitConsumer(channel, role, settings.max_attempts)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Остановка по сигналу: контейнеру он приходит при `docker compose
        # stop`, и текущее сообщение должно успеть доехать, а не оборваться.
        loop.add_signal_handler(sig, stop.set)

    logger.info('Воркер запущен', extra={'role': role})
    consuming = asyncio.create_task(consumer.consume(handler))
    stopping = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait({consuming, stopping}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        # Исключение из чтения очереди должно всплыть, а не потеряться в
        # завершённой задаче.
        if task is consuming and not task.cancelled():
            task.result()

    for delivery_channel in channels.values():
        await delivery_channel.close()
    await auth_client.aclose()
    await connection.close()
    if postgres.engine is not None:
        await postgres.engine.dispose()
    logger.info('Воркер остановлен', extra={'role': role})


def build_channels() -> dict[str, DeliveryChannel]:
    """Каналы доставки, доступные отправителю.

    Без настроенного почтового сервера включается канал-заглушка: стек должен
    подниматься локально и без SMTP, а письмо видно в журнале.
    """
    channels: dict[str, DeliveryChannel] = {}
    if settings.smtp_host:
        pool = SmtpConnectionPool(
            host=settings.smtp_host,
            port=settings.smtp_port,
            use_tls=settings.smtp_use_tls,
            user=settings.smtp_user,
            password=settings.smtp_password.get_secret_value(),
            size=settings.smtp_pool_size,
            timeout=settings.smtp_timeout,
        )
        channels[Channel.EMAIL.value] = EmailChannel(
            pool, settings.smtp_from, RateLimiter(settings.smtp_rate_per_second),
        )
    else:
        logger.warning('Почтовый сервер не задан: письма уходят в журнал')
        channels[Channel.EMAIL.value] = LoggingChannel()
    if settings.websocket_url:
        channels[Channel.WEBSOCKET.value] = WebsocketChannel(
            httpx.AsyncClient(base_url=settings.websocket_url, timeout=settings.auth_timeout),
            settings.auth_service_token.get_secret_value(),
        )
    return channels


def build_handler(
    role: str,
    sessions: async_sessionmaker[AsyncSession],
    publisher: RabbitPublisher,
    auth_client: httpx.AsyncClient,
    channels: dict[str, DeliveryChannel],
):  # noqa: ANN202 - возвращается замыкание с известной сигнатурой обработчика
    """Собирает обработчик сообщений для выбранной роли (Composition Root воркера)."""
    directory = AuthContactDirectory(auth_client, settings.auth_service_token.get_secret_value())
    renderer = Renderer()
    quiet_hours = QuietHours(settings.quiet_hours_start, settings.quiet_hours_end, settings.default_timezone)

    async def handle_plan(payload: dict[str, Any], request_id: str) -> None:
        set_request_id(request_id)
        async with sessions() as session:
            service = PlannerService(
                PostgresTemplateRepository(session),
                PostgresSubscriptionRepository(session),
                PostgresNotificationRepository(session),
                directory,
                publisher,
                settings.batch_size,
            )
            await service.plan(PlanMessage.model_validate(payload))

    async def handle_render(payload: dict[str, Any], request_id: str) -> None:
        set_request_id(request_id)
        async with sessions() as session:
            service = AssemblyService(
                PostgresTemplateRepository(session),
                directory,
                renderer,
                publisher,
                quiet_hours,
                settings.public_base_url,
                settings.jwt_secret_key.get_secret_value(),
            )
            await service.assemble(RenderMessage.model_validate(payload))

    async def handle_send(payload: dict[str, Any], request_id: str) -> None:
        set_request_id(request_id)
        async with sessions() as session:
            service = SenderService(
                PostgresDeliveryRepository(session),
                PostgresNotificationRepository(session),
                channels,
            )
            await service.send(SendMessage.model_validate(payload))

    handlers = {STAGE_PLAN: handle_plan, STAGE_RENDER: handle_render, STAGE_SEND: handle_send}
    return handlers[role]


def main() -> int:
    role = os.environ.get('NOTIFY_WORKER_ROLE', STAGE_SEND)
    if role not in ROLES:
        logger.error('Неизвестная роль воркера: %s. Допустимые: %s', role, ', '.join(ROLES))
        return 2
    asyncio.run(run(role))
    return 0


if __name__ == '__main__':
    sys.exit(main())
