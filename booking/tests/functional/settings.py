"""Адреса тестируемых сервисов.

По умолчанию — порты, которые docker-compose тестов пробрасывает на
localhost. Внутри docker-compose адреса переопределяются переменными окружения.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra='ignore')

    service_url: str = 'http://127.0.0.1:8007'
    # Настоящий сервис авторизации: в нём тесты заводят зрителей, а сервис
    # бронирования берёт их имена.
    auth_url: str = 'http://127.0.0.1:8008'
    # Заглушка соседей: каталог фильмов и приём событий сервисом уведомлений.
    stub_url: str = 'http://127.0.0.1:8009'

    # Через сколько секунд после создания можно назначить показ — с этим
    # значением запущен сервис; тесты оценок ждут начала показа.
    min_lead_seconds: float = 2

    wait_timeout: float = 180
    # Сколько ждать, пока ретранслятор донесёт событие до сервиса уведомлений.
    event_timeout: float = 20


settings = Settings()
