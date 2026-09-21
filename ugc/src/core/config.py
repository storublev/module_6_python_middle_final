"""Настройки сервиса сбора событий."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из переменных окружения или файла .env.

    Префикс UGC_ — потому что .env общий для всех сервисов кинотеатра: без
    префикса `KAFKA_TOPIC` этого сервиса совпал бы с топиком ETL, а
    `LOG_LEVEL` — с уровнем журнала Async API.

    Исключение одно — ключ подписи токенов: он тот же, что у сервиса
    авторизации (`AUTH_JWT_SECRET_KEY`), потому что подпись проверяется тем же
    секретом, которым выпущена (ADR-4). Значения по умолчанию у него нет:
    сервис не должен стартовать с общеизвестным ключом.
    """

    model_config = SettingsConfigDict(env_prefix='UGC_', env_file='.env', extra='ignore')

    project_name: str = 'ugc'
    log_level: str = 'INFO'

    kafka_bootstrap_servers: str = 'localhost:9092'
    kafka_topic: str = 'ugc.events'
    # acks=all — ждём записи во все синхронизированные реплики. Событие,
    # подтверждённое клиенту, не должно пропасть при падении одного брокера.
    kafka_acks: str = 'all'
    # Продюсер ждёт несколько миллисекунд и склеивает сообщения в один запрос.
    kafka_linger_ms: int = Field(default=10, ge=0)
    kafka_batch_size: int = Field(default=64 * 1024, ge=1024)
    kafka_compression: str | None = 'lz4'
    # Пока брокер молчит, ждёт и клиент, поэтому таймауты короткие.
    kafka_request_timeout_ms: int = Field(default=5000, ge=100)
    kafka_max_block_ms: int = Field(default=5000, ge=100)
    kafka_retries: int = Field(default=3, ge=0)
    kafka_flush_timeout: float = Field(default=5.0, gt=0)

    # Ключ подписи access-токенов сервиса авторизации. Для HS256 ключ короче
    # 32 байт подбирается быстрее, чем стоило бы.
    jwt_secret_key: SecretStr = Field(min_length=32, validation_alias='AUTH_JWT_SECRET_KEY')
    jwt_algorithm: str = 'HS256'

    # Сколько событий принимаем в одном запросе. Предел защищает и сервис (от
    # запроса на сто мегабайт), и хранилище: клиент отправляет пачки не реже
    # раза в 10 секунд, и 200 событий за это время не накопит ни один зритель.
    max_events_per_request: int = Field(default=200, ge=1)

    # Распределённая трассировка. Пустой адрес выключает отправку спанов:
    # сервис, запущенный локально без Jaeger, должен работать как обычно.
    otlp_endpoint: str = ''
    # Идентификатор запроса ставит nginx, поэтому его отсутствие значит, что
    # запрос пришёл мимо шлюза, — и по умолчанию такой запрос отклоняется.
    require_request_id: bool = True


settings = Settings()
