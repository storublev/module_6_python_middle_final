"""Генератор автоматических событий.

Просыпается раз в минуту, смотрит, каким рассылкам пора, и публикует события.
Это тот самый компонент, который урок «Генерация автоматических событий»
называет шедулером: периодические уведомления рождаются не от действия
пользователя, а от наступления времени.

Почему собственный цикл, а не cron контейнера. Урок предлагает оба варианта.
Свой цикл выигрывает в одном: он ходит в ту же базу, что и рассылки, и
защита от повторов работает на уровне данных, а не на уровне «не запускай
скрипт дважды». Два экземпляра генератора, поднятые одновременно, не разошлют
одно и то же — ключ запуска `(рассылка, период)` уникален.

В RabbitMQ генератор не пишет: запуск рассылки и задание на публикацию
ложатся в базу одной транзакцией, а в брокер их переносит ретранслятор
(`relay.py`). Поэтому лежащий брокер не «съедает» запуск.

Отсюда же следует, что простой генератора не превращается в лавину: проснувшись
через сутки, он увидит, что запуски за прошедшие периоды уже отмечены, и
разошлёт только то, что действительно не ушло (НФТ-5).
"""

import asyncio
import logging
import signal
from datetime import datetime, timezone
from logging.config import dictConfig

from sqlalchemy.ext.asyncio import async_sessionmaker

import db.postgres as postgres
from core.config import settings
from core.logger import LOGGING
from core.sentry import configure_sentry
from services.campaigns import CampaignService
from storage.base import StorageUnavailableError
from storage.postgres import PostgresCampaignRepository, PostgresTemplateRepository

dictConfig(LOGGING)
logger = logging.getLogger(__name__)

# Шаг цикла. Минута — самая мелкая единица расписания cron, чаще просыпаться
# незачем.
TICK_SECONDS = 60


async def run() -> None:
    configure_sentry(settings.sentry_dsn, f'{settings.project_name}-scheduler', settings.sentry_environment)
    postgres.engine = postgres.create_engine(settings)
    sessions = async_sessionmaker(postgres.engine, expire_on_commit=False)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info('Генератор автоматических событий запущен')
    while not stop.is_set():
        try:
            await tick(sessions)
        except StorageUnavailableError as error:
            # База прилегла: ждём следующего шага. Пропущенные
            # периоды не потеряются — их подхватит следующий проход.
            logger.warning('Шаг генератора пропущен: %s', error)
        except Exception:
            logger.exception('Шаг генератора завершился ошибкой')
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            # Именно asyncio.TimeoutError, а не встроенный TimeoutError: их
            # объединили только в Python 3.11, а сервис должен работать и на
            # 3.10 — его версии проверяет матрица в CI.
            continue

    if postgres.engine is not None:
        await postgres.engine.dispose()
    logger.info('Генератор остановлен')


async def tick(sessions: async_sessionmaker) -> int:  # noqa: ANN001
    """Один проход: запускает всё, чему пора. Возвращает число запущенных рассылок."""
    moment = datetime.now(timezone.utc)
    async with sessions() as session:
        service = CampaignService(
            PostgresCampaignRepository(session),
            PostgresTemplateRepository(session),
        )
        launched = await service.launch_due(moment)
    if launched:
        logger.info('Запущено рассылок: %s', launched)
    return launched


if __name__ == '__main__':
    asyncio.run(run())
