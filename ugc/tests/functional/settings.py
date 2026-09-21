from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Адреса тестируемого сервиса и брокера.

    По умолчанию — порты, которые docker-compose тестов пробрасывает на
    localhost. Внутри docker-compose адреса переопределяются переменными
    окружения.
    """

    model_config = SettingsConfigDict(extra='ignore')

    service_url: str = 'http://127.0.0.1:8002'

    kafka_bootstrap_servers: str = 'localhost:29092'
    kafka_topic: str = 'ugc.events'

    # Тот же ключ, что у сервиса в docker-compose тестов: им подписываются и
    # обычные токены, и истёкшие, которых иначе пришлось бы ждать 15 минут.
    jwt_secret_key: str = 'functional-tests-secret-key-of-32-bytes'

    # Предел пачки, с которым запущен сервис в docker-compose тестов.
    max_events_per_request: int = 5

    # Сколько секунд ждать готовности сервиса и брокера перед тестами.
    wait_timeout: float = 90


settings = Settings()
