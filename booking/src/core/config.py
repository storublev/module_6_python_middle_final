"""Настройки сервиса бронирования.

Префикс BOOKING_ — потому что .env общий для всех сервисов кинотеатра: без него
`POSTGRES_DB` этого сервиса столкнулся бы с базой фильмов.

Два исключения из префикса — общие секреты сервиса авторизации: ключ подписи
access-токенов (`AUTH_JWT_SECRET_KEY`) и служебный секрет (`AUTH_SERVICE_TOKEN`),
которым сервис ходит в справочник контактов и в API уведомлений. Своих копий с
другими именами у них быть не должно.

Значений по умолчанию у секретов нет: сервис не должен стартовать с
общеизвестным ключом.
"""

from datetime import timedelta
from typing import Annotated

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _duration(value: object) -> object:
    """Разрешает писать длительности числом секунд: BOOKING_RETRY_DELAY=60."""
    if isinstance(value, (int, float)):
        return timedelta(seconds=value)
    if isinstance(value, str) and value.replace('.', '', 1).isdigit():
        return timedelta(seconds=float(value))
    return value


Duration = Annotated[timedelta, BeforeValidator(_duration)]


class Settings(BaseSettings):
    """Настройки из переменных окружения или файла .env."""

    model_config = SettingsConfigDict(env_prefix='BOOKING_', env_file='.env', extra='ignore')

    project_name: str = 'booking'
    log_level: str = 'INFO'

    postgres_host: str = '127.0.0.1'
    postgres_port: int = 5432
    postgres_db: str = 'booking'
    postgres_user: str = 'booking'
    postgres_password: SecretStr
    postgres_echo: bool = False
    # Пул соединений на процесс uvicorn. Запросы короткие, и десяти хватает
    # на сотни запросов в секунду; больше — только лишние соединения у базы.
    postgres_pool_size: int = Field(default=10, ge=1)

    # Ключ подписи access-токенов: тот же, которым их выпускает сервис авторизации.
    jwt_secret_key: SecretStr = Field(min_length=32, validation_alias='AUTH_JWT_SECRET_KEY')
    jwt_algorithm: str = 'HS256'
    # Служебный секрет: справочник контактов сервиса авторизации и приём
    # событий сервисом уведомлений пускают по нему.
    service_token: SecretStr = Field(default=SecretStr(''), validation_alias='AUTH_SERVICE_TOKEN')

    # Каталог фильмов: проверить, что фильм есть и его можно бронировать.
    catalog_url: str = 'http://api:8000'
    catalog_timeout: float = Field(default=3.0, gt=0)
    # Справочник контактов сервиса авторизации: имена хоста и гостя.
    auth_url: str = 'http://auth:8000'
    auth_timeout: float = Field(default=2.0, gt=0)
    # API уведомлений: письма участникам. Пустой адрес выключает письма —
    # события копятся в outbox и уйдут, когда адрес появится.
    notify_url: str = 'http://notify-api:8000'
    notify_timeout: float = Field(default=5.0, gt=0)

    # Правила показа (ФТ-6): камерные посиделки, не в прошлом и не через годы.
    min_capacity: int = Field(default=1, ge=1)
    max_capacity: int = Field(default=50, ge=1)
    # Сколько мест гость берёт за раз: на себя и пару друзей, а не весь зал.
    max_seats_per_booking: int = Field(default=10, ge=1)
    min_lead_time: Duration = timedelta(minutes=30)
    max_lead_time: Duration = timedelta(days=365)

    # Публичный адрес кинотеатра: ссылки на показ в письмах.
    public_base_url: str = 'http://localhost'
    # В каком поясе писать время показа в письмах. В базе время в UTC (НФТ-12).
    display_timezone: str = 'Europe/Moscow'

    # Ретранслятор outbox: как часто заглядывать в базу, сколько событий брать
    # за раз, на сколько откладывать на время отправки и после неудачи.
    outbox_poll_interval: float = Field(default=1.0, gt=0)
    outbox_batch_size: int = Field(default=50, ge=1)
    outbox_lease: Duration = timedelta(seconds=30)
    retry_delay: Duration = timedelta(seconds=30)

    page_size_default: int = Field(default=20, ge=1)
    page_size_max: int = Field(default=100, ge=1)

    # Идентификатор запроса ставит nginx, поэтому его отсутствие значит, что
    # запрос пришёл мимо шлюза, — и по умолчанию такой запрос отклоняется.
    require_request_id: bool = True

    sentry_dsn: str = ''
    sentry_environment: str = 'local'
    otlp_endpoint: str = ''

    @property
    def postgres_dsn(self) -> str:
        return (
            f'postgresql+asyncpg://{self.postgres_user}:{self.postgres_password.get_secret_value()}'
            f'@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}'
        )


# Обязательные поля без значений по умолчанию pydantic-settings берёт из
# окружения, а mypy видит только сигнатуру и считает их пропущенными
# аргументами — отсюда точечное умолчание.
settings = Settings()  # type: ignore[call-arg]
