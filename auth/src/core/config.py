from datetime import timedelta

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки сервиса авторизации из переменных окружения или файла .env.

    У всех переменных префикс AUTH_: сервис делит .env с остальными сервисами
    кинотеатра, и без префикса его POSTGRES_DB совпал бы с базой фильмов.
    Секреты (пароль PostgreSQL, ключ подписи JWT) значений по умолчанию не
    имеют: без них сервис не стартует, а не работает с общеизвестным ключом.
    """

    model_config = SettingsConfigDict(env_prefix='AUTH_', env_file='.env', extra='ignore')

    project_name: str = 'auth'
    log_level: str = 'INFO'

    postgres_host: str = '127.0.0.1'
    postgres_port: int = 5432
    postgres_db: str = 'auth'
    postgres_user: str = 'auth'
    postgres_password: SecretStr
    # Писать SQL-запросы в журнал — только для отладки.
    postgres_echo: bool = False

    redis_host: str = '127.0.0.1'
    redis_port: int = 6379
    redis_db: int = 0
    # Повторы при обрыве соединения с Redis: без него не проверить сессию,
    # поэтому короткий сбой (перезапуск) стоит переждать, но не дольше доли секунды.
    redis_backoff_retries: int = 2
    redis_backoff_base: float = 0.05
    redis_backoff_cap: float = 0.5

    # Для HS256 ключ короче 32 байт (256 бит) подбирается быстрее, чем стоило бы.
    jwt_secret_key: SecretStr = Field(min_length=32)
    # HS256 — HMAC-SHA256: один секрет и подписывает, и проверяет токен.
    jwt_algorithm: str = 'HS256'
    # Время жизни задаётся числом секунд или в ISO 8601 (PT15M).
    # access-токен не хранится и не отзывается сам по себе, поэтому живёт
    # недолго; refresh-токен одноразовый и хранится в Redis вместе с сессией.
    access_token_ttl: timedelta = timedelta(minutes=15)
    refresh_token_ttl: timedelta = timedelta(days=14)

    # Сколько хранится в кеше набор прав пользователя. Изменения ролей
    # сбрасывают кеш сразу, время жизни — страховка от забытых записей.
    access_cache_ttl: timedelta = timedelta(minutes=10)

    @property
    def postgres_dsn(self) -> str:
        return (
            f'postgresql+asyncpg://{self.postgres_user}:{self.postgres_password.get_secret_value()}'
            f'@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}'
        )


settings = Settings()
