"""Адреса тестируемых сервисов.

По умолчанию — порты, которые docker-compose тестов пробрасывает на
localhost. Внутри docker-compose адреса переопределяются переменными
окружения.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки функциональных тестов."""

    model_config = SettingsConfigDict(extra='ignore')

    service_url: str = 'http://127.0.0.1:8005'
    ws_url: str = 'ws://127.0.0.1:8006'
    # Настоящий сервис авторизации: у него тесты заводят зрителя и берут его
    # токен, а воркер — его контакты.
    auth_url: str = 'http://127.0.0.1:8004'
    # Приёмник почты с HTTP-API: через него тест читает письмо целиком.
    mailpit_url: str = 'http://127.0.0.1:8026'
    # Панель управления брокером: по ней тест смотрит длину очередей.
    rabbit_ui_url: str = 'http://notify:functional-tests@127.0.0.1:15673'

    service_token: str = 'functional-tests-service-token'
    jwt_secret_key: str = 'functional-tests-secret-key-of-32-bytes'

    # Размер пачки, с которым запущен сервис в docker-compose тестов.
    batch_size: int = 2

    # Сколько секунд ждать готовности сервисов перед тестами.
    wait_timeout: float = 180
    # Сколько ждать письма: оно проходит три очереди, и мгновенным быть не обязано.
    mail_timeout: float = 30


settings = Settings()
