import re
from datetime import timedelta
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SECONDS_PATTERN = re.compile(r'\d+(\.\d+)?')


def seconds_to_timedelta(value: Any) -> Any:
    """Число секунд строкой — как timedelta.

    Переменные окружения — всегда строки, а строку «900» pydantic не считает
    числом секунд: принимает только ISO 8601 (PT15M) и чч:мм:сс.
    """
    if isinstance(value, str) and SECONDS_PATTERN.fullmatch(value.strip()):
        return float(value)
    return value


# Время жизни и интервалы задаются числом секунд или в ISO 8601 (PT15M).
Duration = Annotated[timedelta, BeforeValidator(seconds_to_timedelta)]


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
    # access-токен не хранится и не отзывается сам по себе, поэтому живёт
    # недолго; refresh-токен одноразовый и хранится в Redis вместе с сессией.
    access_token_ttl: Duration = timedelta(minutes=15)
    refresh_token_ttl: Duration = timedelta(days=14)
    # Сколько сессий (устройств) может быть у пользователя одновременно. Вход
    # сверх предела закрывает сессии, которые дольше всех не продлевались.
    max_sessions_per_user: int = Field(default=20, ge=1)

    # Общий секрет для служебного входа сервиса уведомлений: он ходит за
    # контактами чужих пользователей, и опознавать его access-токеном
    # некого — своего пользователя у сервиса нет. Пустое значение выключает
    # служебный справочник целиком: его эндпоинты отвечают 404, как и
    # эндпоинты незаданного поставщика OAuth.
    service_token: SecretStr = SecretStr('')

    # Сколько хранится в кеше набор прав пользователя. Изменения ролей
    # сбрасывают кеш сразу, время жизни — страховка от забытых записей.
    access_cache_ttl: Duration = timedelta(minutes=10)
    # Как часто фоновая задача повторяет сброс кеша прав, если сразу после
    # изменения ролей Redis был недоступен. При сбоях пауза растёт до max.
    access_invalidation_interval: Duration = timedelta(seconds=5)
    access_invalidation_max_interval: Duration = timedelta(minutes=1)

    # Лимиты попыток входа и регистрации: не больше N попыток за скользящее окно.
    # С одного адреса входят в разные аккаунты (NAT, офис), поэтому лимит по
    # IP мягче лимита по логину. Лимит логина считает неудачные попытки:
    # успешный вход его обнуляет.
    login_attempts_per_ip: int = Field(default=20, ge=1)
    login_attempts_per_ip_period: Duration = timedelta(minutes=1)
    login_attempts_per_account: int = Field(default=10, ge=1)
    login_attempts_per_account_period: Duration = timedelta(minutes=15)
    signup_attempts_per_ip: int = Field(default=10, ge=1)
    signup_attempts_per_ip_period: Duration = timedelta(hours=1)
    # Подтверждение пароля в личном кабинете (смена логина и пароля) — та же
    # проверка пароля, что и вход, поэтому ограничено так же. Лимит учётной
    # записи строже лимита входа: свой пароль владелец знает, а перебирать
    # его здесь можно только с действующим токеном.
    password_check_attempts_per_account: int = Field(default=5, ge=1)
    password_check_attempts_per_account_period: Duration = timedelta(minutes=15)
    password_check_attempts_per_ip: int = Field(default=20, ge=1)
    password_check_attempts_per_ip_period: Duration = timedelta(minutes=15)

    # Вход через соцсети. Поставщик включён, только если заданы обе его
    # переменные: без ключей приложения ходить к нему не с чем, и тогда его
    # эндпоинты отвечают 404. Ключи выдаёт сам поставщик при регистрации
    # приложения — секретами они и остаются, значений по умолчанию нет.
    oauth_yandex_client_id: str = ''
    oauth_yandex_client_secret: SecretStr = SecretStr('')
    oauth_google_client_id: str = ''
    oauth_google_client_secret: SecretStr = SecretStr('')
    # Адрес, на который поставщик возвращает пользователя. Он же прописывается
    # в настройках приложения у поставщика и должен совпадать с ним посимвольно.
    oauth_redirect_base_url: str = 'http://localhost'
    # Сколько живёт начатый вход: пользователю хватит, чтобы войти у
    # поставщика, а брошенный вход не будет ждать вечно.
    oauth_state_ttl: Duration = timedelta(minutes=10)
    # Ждать поставщика долго нельзя: пока он думает, ждёт и пользователь.
    oauth_request_timeout: float = 5

    # Распределённая трассировка. Пустой адрес выключает отправку спанов:
    # сервис, запущенный локально без Jaeger, должен работать как обычно.
    otlp_endpoint: str = ''
    # Идентификатор запроса ставит nginx, поэтому его отсутствие значит, что
    # запрос пришёл мимо шлюза. Без него запрос не найти ни в журналах, ни в
    # Jaeger, поэтому по умолчанию такие запросы отклоняются.
    require_request_id: bool = True

    @property
    def postgres_dsn(self) -> str:
        return (
            f'postgresql+asyncpg://{self.postgres_user}:{self.postgres_password.get_secret_value()}'
            f'@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}'
        )
    # Адрес Sentry. Пустая строка выключает отправку: сервис, запущенный
    # локально без Sentry, должен работать как обычно.
    sentry_dsn: str = ''
    sentry_environment: str = 'local'


# Обязательные поля без значений по умолчанию pydantic-settings берёт из
# окружения, а mypy видит только сигнатуру и считает их пропущенными
# аргументами — отсюда точечное умолчание.
settings = Settings()  # type: ignore[call-arg]
