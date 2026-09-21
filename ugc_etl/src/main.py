"""Точка входа ETL: собирает участников и запускает перенос.

Composition Root: здесь и только здесь выбирается, что стоит за интерфейсами
источника и приёмника. Бизнес-логика (`services/pipeline.py`) об этом не знает.

Остановка — по сигналу от оркестратора: контейнеру при `docker compose down`
приходит SIGTERM, и перенос должен закончить текущую пачку, а не оборваться
посреди неё.
"""

import logging
import signal
from logging.config import dictConfig

from core.config import Settings, settings
from core.logger import LOGGING
from core.memory import MemoryWatch
from services.pipeline import EventPipeline, RetryPolicy
from storage.clickhouse import ClickHouseEventSink
from storage.kafka import KafkaEventSource
from storage.resilience import CircuitBreaker

logger = logging.getLogger(__name__)


def build_pipeline(config: Settings) -> tuple[EventPipeline, KafkaEventSource, ClickHouseEventSink]:
    """Создаёт перенос и его участников."""
    source = KafkaEventSource(
        config.kafka_bootstrap_servers,
        config.kafka_topic,
        config.kafka_group_id,
        auto_offset_reset=config.kafka_auto_offset_reset,
        batch_size=config.batch_size,
        poll_timeout=config.poll_timeout,
    )
    sink = ClickHouseEventSink(
        host=config.clickhouse_host,
        port=config.clickhouse_port,
        user=config.clickhouse_user,
        password=config.clickhouse_password.get_secret_value(),
        database=config.clickhouse_database,
        table=config.clickhouse_table,
        connect_timeout=config.clickhouse_connect_timeout,
        send_receive_timeout=config.clickhouse_send_receive_timeout,
    )
    pipeline = EventPipeline(
        source=source,
        sink=sink,
        memory=MemoryWatch(report_every=config.memory_report_every, limit_mb=config.memory_limit_mb),
        policy=RetryPolicy(
            retries=config.insert_retries,
            base=config.insert_backoff_base,
            cap=config.insert_backoff_cap,
        ),
        breaker=CircuitBreaker(config.breaker_failures, config.breaker_reset_timeout),
        breaker_pause=config.breaker_reset_timeout,
    )
    return pipeline, source, sink


def main() -> None:
    dictConfig(LOGGING)
    pipeline, source, sink = build_pipeline(settings)

    def shutdown(signum, _frame) -> None:
        logger.info('Получен сигнал %s, заканчиваем текущую пачку', signal.Signals(signum).name)
        pipeline.stop()
        source.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        pipeline.run()
    finally:
        source.close()
        sink.close()


if __name__ == '__main__':
    main()
