"""Настройки интерфейса кинотеатра.

Префикс WEB_ — потому что .env общий для всех сервисов кинотеатра. Ключ
подписи access-токенов (`AUTH_JWT_SECRET_KEY`) общий с сервисом авторизации:
интерфейс проверяет токен на месте, как и остальные сервисы (ADR-4), и не
ходит в Auth на каждую страницу.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='WEB_', env_file='.env', extra='ignore')

    project_name: str = 'web'
    log_level: str = 'INFO'

    # Внутренние адреса API — в сети compose, мимо nginx: интерфейс сам
    # передаёт им X-Request-Id, а лишний проход через шлюз только добавил бы
    # задержку к пределу в 300 мс.
    catalog_url: str = 'http://api:8000'
    booking_url: str = 'http://booking-api:8000'
    auth_url: str = 'http://auth:8000'
    # Таймаут на запрос к API. Страница ходит в два-три сервиса параллельно и
    # должна уложиться в 300 мс, поэтому ждать дольше секунды бессмысленно:
    # лучше показать страницу без блока, чем не показать её вовсе.
    api_timeout: float = Field(default=1.0, gt=0)

    jwt_secret_key: SecretStr = Field(min_length=32, validation_alias='AUTH_JWT_SECRET_KEY')
    jwt_algorithm: str = 'HS256'

    # В каком поясе показывать время и понимать время из формы показа.
    timezone: str = 'Europe/Moscow'
    # Secure-флаг cookie: на стенде сайт открывается по http, в бою — только https.
    cookie_secure: bool = False
    # Сколько фильмов на странице каталога: сетка 6 × 4.
    catalog_page_size: int = Field(default=24, ge=1, le=100)

    require_request_id: bool = True
    sentry_dsn: str = ''
    sentry_environment: str = 'local'
    otlp_endpoint: str = ''


settings = Settings()  # type: ignore[call-arg]
