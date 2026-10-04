from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Адреса тестируемого API и хранилищ.

    По умолчанию — порты, которые docker-compose тестов пробрасывает на
    localhost: так тесты можно запускать из IDE против поднятых контейнеров.
    Внутри docker-compose адреса переопределяются переменными окружения.
    """

    model_config = SettingsConfigDict(extra='ignore')

    elastic_url: str = 'http://127.0.0.1:9200'
    redis_host: str = '127.0.0.1'
    redis_port: int = 6379
    service_url: str = 'http://127.0.0.1:8000'

    # Сервис авторизации: тесты заводят в нём пользователей и выдают подписку,
    # чтобы проверить доступ к подписочным фильмам.
    auth_url: str = 'http://127.0.0.1:8001'
    # Суперпользователь, которого создаёт одноразовый контейнер окружения:
    # под ним назначаются роли, для этого нужно право access.manage.
    auth_superuser_login: str = 'admin'
    auth_superuser_password: str = 'functional-tests-admin'
    # Роль с правом films.subscription; её заводит миграция сервиса авторизации.
    subscribers_role: str = 'subscribers'

    # Сколько секунд ждать готовности Elasticsearch и Redis перед тестами.
    wait_timeout: float = 60


settings = Settings()
