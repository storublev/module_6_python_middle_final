"""Настройки сервиса пользовательского контента.

Префикс CONTENT_ — потому что .env общий для всех сервисов кинотеатра: без
префикса `MONGO_URI` этого сервиса столкнулся бы с настройками исследования, а
`LOG_LEVEL` — с уровнем журнала Async API.

Исключение одно — ключ подписи токенов: он тот же, что у сервиса авторизации
(`AUTH_JWT_SECRET_KEY`), потому что подпись проверяется тем же секретом,
которым выпущена. Значения по умолчанию у него нет: сервис не должен
стартовать с общеизвестным ключом.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из переменных окружения или файла .env."""

    model_config = SettingsConfigDict(env_prefix='CONTENT_', env_file='.env', extra='ignore')

    project_name: str = 'ugc-content'
    log_level: str = 'INFO'

    # Адрес маршрутизатора mongos, а не узла шарда: клиент обязан ходить через
    # mongos, иначе увидит только часть данных шардированной коллекции.
    mongo_uri: str = 'mongodb://localhost:27017'
    mongo_database: str = 'ugc_content'
    # Пока брокер запросов молчит, ждёт и пользователь, а бюджет ответа — 200 мс
    # на всё, поэтому таймауты короткие и заданы явно.
    mongo_connect_timeout_ms: int = Field(default=2000, ge=100)
    mongo_server_selection_timeout_ms: int = Field(default=2000, ge=100)
    mongo_socket_timeout_ms: int = Field(default=2000, ge=100)

    # Ключ подписи access-токенов сервиса авторизации. Для HS256 ключ короче
    # 32 байт подбирается быстрее, чем стоило бы.
    jwt_secret_key: SecretStr = Field(min_length=32, validation_alias='AUTH_JWT_SECRET_KEY')
    jwt_algorithm: str = 'HS256'

    # Пределы страниц: список рецензий фильма может быть длинным, но отдавать
    # его целиком — верный способ не уложиться в 200 мс.
    page_size_default: int = Field(default=20, ge=1)
    page_size_max: int = Field(default=100, ge=1)

    # Идентификатор запроса ставит nginx, поэтому его отсутствие значит, что
    # запрос пришёл мимо шлюза, — и по умолчанию такой запрос отклоняется.
    require_request_id: bool = True

    # Адрес Sentry. Пустая строка выключает отправку: сервис, запущенный
    # локально без Sentry, должен работать как обычно.
    sentry_dsn: str = ''
    sentry_environment: str = 'local'

    # Распределённая трассировка. Пустой адрес выключает отправку спанов:
    # сервис, запущенный локально без Jaeger, должен работать как обычно.
    otlp_endpoint: str = ''


settings = Settings()
