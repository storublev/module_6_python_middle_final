"""Адреса тестируемого сервиса и хранилища.

По умолчанию — порты, которые docker-compose тестов пробрасывает на
localhost. Внутри docker-compose адреса переопределяются переменными
окружения.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки функциональных тестов."""

    model_config = SettingsConfigDict(extra='ignore')

    service_url: str = 'http://127.0.0.1:8003'

    mongo_uri: str = 'mongodb://localhost:27020'
    mongo_database: str = 'ugc_content'

    # Тот же ключ, что у сервиса в docker-compose тестов: им подписываются и
    # обычные токены, и истёкшие, которых иначе пришлось бы ждать 15 минут.
    jwt_secret_key: str = 'functional-tests-secret-key-of-32-bytes'

    # Пределы страницы, с которыми запущен сервис в docker-compose тестов.
    page_size_default: int = 20
    page_size_max: int = 50

    # Сколько секунд ждать готовности сервиса и хранилища перед тестами.
    wait_timeout: float = 90


settings = Settings()
