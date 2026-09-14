from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки сервиса, читаются из переменных окружения или файла .env."""

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    project_name: str = 'movies'
    log_level: str = 'INFO'

    redis_host: str = '127.0.0.1'
    redis_port: int = 6379

    elastic_host: str = '127.0.0.1'
    elastic_port: int = 9200

    # Время жизни записей кеша в секундах.
    cache_expire_in_seconds: int = 60 * 5

    # Сколько секунд ждать ответа Elasticsearch на одну попытку.
    elastic_request_timeout: float = 2

    # Повторы при отказе в соединении с Elasticsearch: пауза между попытками
    # растёт экспоненциально, повторы прекращаются через max_time секунд.
    # Без Elasticsearch ответить нечем, поэтому короткий сбой стоит переждать.
    elastic_backoff_max_time: float = 3
    elastic_backoff_factor: float = 0.1
    elastic_backoff_max_value: float = 1

    # Повторы при обрыве соединения с Redis. Кеш не должен задерживать ответ:
    # одного повтора хватает, чтобы переподключиться после перезапуска Redis,
    # паузы короткие, а таймауты не повторяются вовсе.
    redis_backoff_retries: int = 1
    redis_backoff_base: float = 0.01
    redis_backoff_cap: float = 0.1

    @property
    def elastic_url(self) -> str:
        return f'http://{self.elastic_host}:{self.elastic_port}'


settings = Settings()
