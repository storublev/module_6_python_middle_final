from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Адреса тестируемого сервиса и его хранилищ.

    По умолчанию — порты, которые docker-compose тестов пробрасывает на
    localhost. Внутри docker-compose адреса переопределяются переменными окружения.
    """

    model_config = SettingsConfigDict(extra='ignore')

    service_url: str = 'http://127.0.0.1:8001'

    postgres_host: str = '127.0.0.1'
    postgres_port: int = 5433
    postgres_db: str = 'auth'
    postgres_user: str = 'auth'
    postgres_password: str = 'auth-tests'

    redis_host: str = '127.0.0.1'
    redis_port: int = 6380

    # Тот же ключ, что у сервиса в docker-compose тестов: им подписываются
    # истёкшие токены, которые иначе пришлось бы ждать 15 минут.
    jwt_secret_key: str = 'functional-tests-secret-key-of-32-bytes'

    # Лимиты попыток, с которыми запущен сервис в docker-compose тестов.
    login_attempts_per_ip: int = 10
    login_attempts_per_account: int = 5
    signup_attempts_per_ip: int = 5

    # Сколько секунд ждать готовности сервиса и хранилищ перед тестами.
    wait_timeout: float = 60

    @property
    def postgres_dsn(self) -> str:
        return (
            f'postgresql://{self.postgres_user}:{self.postgres_password}'
            f'@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}'
        )


settings = Settings()
