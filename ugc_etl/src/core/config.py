"""Настройки ETL событий."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из переменных окружения или файла .env.

    Префикс UGC_ETL_ — потому что .env общий для всех сервисов кинотеатра:
    без него `KAFKA_TOPIC` этого сервиса совпал бы с топиком сервиса приёма,
    а `LOG_LEVEL` — с уровнем журнала Async API.
    """

    model_config = SettingsConfigDict(env_prefix='UGC_ETL_', env_file='.env', extra='ignore')

    project_name: str = 'ugc-etl'
    log_level: str = 'INFO'

    kafka_bootstrap_servers: str = 'localhost:9092'
    kafka_topic: str = 'ugc.events'
    # Группа потребителей: все экземпляры ETL входят в одну, и Kafka сама
    # раздаёт им партиции. Добавили экземпляр — партиции перераспределились.
    kafka_group_id: str = 'ugc-etl'
    # С начала топика: при первом запуске нужно забрать всё, что уже накопилось.
    kafka_auto_offset_reset: str = 'earliest'
    # Сколько событий забирать за один вызов. Совпадает с размером пачки
    # вставки: пачка читается, вставляется и подтверждается целиком.
    batch_size: int = Field(default=10_000, ge=1)
    # Сколько ждать одного ответа брокера. Это не срок накопления пачки:
    # poll() возвращается, как только в буфере что-то появилось.
    poll_timeout: float = Field(default=5.0, gt=0)
    # А вот это — срок накопления. Поток неравномерный, и ночью пачка не
    # наберётся никогда; ждать её дольше нельзя: НФТ-6 отводит на доставку
    # события пять минут. Меньше ставить тоже не стоит: частые мелкие вставки
    # плодят куски в MergeTree, а их слияние стоит дороже самой вставки.
    batch_max_wait: float = Field(default=30.0, gt=0)

    clickhouse_host: str = 'localhost'
    clickhouse_port: int = 8123
    clickhouse_user: str = 'default'
    clickhouse_password: SecretStr = SecretStr('')
    clickhouse_database: str = 'ugc'
    clickhouse_table: str = 'events'
    clickhouse_connect_timeout: float = Field(default=5.0, gt=0)
    clickhouse_send_receive_timeout: float = Field(default=60.0, gt=0)

    # Повторы вставки с экспоненциальной паузой: короткий сбой хранилища
    # (перезапуск, слияние кусков) стоит переждать.
    insert_retries: int = Field(default=5, ge=0)
    insert_backoff_base: float = Field(default=0.5, gt=0)
    insert_backoff_cap: float = Field(default=30.0, gt=0)
    # Прерыватель: после стольких сбоев подряд ETL перестаёт долбить хранилище
    # и ждёт, а события копятся в Kafka — там они живут неделю.
    breaker_failures: int = Field(default=5, ge=1)
    breaker_reset_timeout: float = Field(default=30.0, gt=0)

    # Мониторинг памяти: сколько пачек между замерами и при каком потреблении
    # считать, что приложение потекло (НФТ-12).
    memory_report_every: int = Field(default=10, ge=1)
    memory_limit_mb: int = Field(default=512, ge=1)

    @property
    def clickhouse_dsn(self) -> str:
        return f'http://{self.clickhouse_host}:{self.clickhouse_port}/{self.clickhouse_database}'
    # Адрес Sentry. Пустая строка выключает отправку: сервис, запущенный
    # локально без Sentry, должен работать как обычно.
    sentry_dsn: str = ''
    sentry_environment: str = 'local'


settings = Settings()
