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

    @property
    def elastic_url(self) -> str:
        return f'http://{self.elastic_host}:{self.elastic_port}'


settings = Settings()
