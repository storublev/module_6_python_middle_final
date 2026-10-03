"""Настройки сервиса уведомлений.

Префикс NOTIFY_ — потому что .env общий для всех сервисов кинотеатра: без него
`POSTGRES_DB` этого сервиса столкнулся бы с базой фильмов, а `LOG_LEVEL` — с
уровнем журнала Async API.

Два исключения из префикса — общие секреты: ключ подписи access-токенов
(`AUTH_JWT_SECRET_KEY`) и служебный секрет для справочника контактов
(`AUTH_SERVICE_TOKEN`). Оба принадлежат сервису авторизации, и своих копий с
другими именами у них быть не должно.

Значений по умолчанию у секретов нет: сервис не должен стартовать с
общеизвестным ключом.
"""

from datetime import timedelta
from typing import Annotated

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _duration(value: object) -> object:
    """Разрешает писать длительности числом секунд: NOTIFY_RETRY_DELAY=60."""
    if isinstance(value, (int, float)):
        return timedelta(seconds=value)
    if isinstance(value, str) and value.replace('.', '', 1).isdigit():
        return timedelta(seconds=float(value))
    return value


Duration = Annotated[timedelta, BeforeValidator(_duration)]


class Settings(BaseSettings):
    """Настройки из переменных окружения или файла .env."""

    model_config = SettingsConfigDict(env_prefix='NOTIFY_', env_file='.env', extra='ignore')

    project_name: str = 'notifications'
    log_level: str = 'INFO'

    postgres_host: str = '127.0.0.1'
    postgres_port: int = 5432
    postgres_db: str = 'notifications'
    postgres_user: str = 'notifications'
    postgres_password: SecretStr
    postgres_echo: bool = False

    # Брокер. Кредиты в адресе, потому что RabbitMQ без них не пускает даже
    # локально, а разбирать адрес на части и собирать обратно незачем.
    rabbit_url: SecretStr
    # Сколько сообщений воркер берёт из очереди, не подтвердив предыдущие.
    # Единица означает «одно письмо за раз»: так медленный отправитель не
    # набирает себе очередь, которую не успевает разобрать, а свободный воркер
    # получает работу вместо него.
    rabbit_prefetch: int = Field(default=16, ge=1)
    # Сколько ждать подтверждения публикации. Меньше аренды задания outbox:
    # иначе задание вернётся к другому ретранслятору, пока первый ещё ждёт.
    rabbit_publish_timeout: float = Field(default=10.0, gt=0)

    # Сервис авторизации: адрес и служебный секрет для справочника контактов.
    auth_url: str = 'http://auth:8000'
    auth_timeout: float = Field(default=5.0, gt=0)
    auth_service_token: SecretStr = Field(default=SecretStr(''), validation_alias='AUTH_SERVICE_TOKEN')
    # Ключ подписи access-токенов: тот же, которым их выпускает сервис авторизации.
    jwt_secret_key: SecretStr = Field(min_length=32, validation_alias='AUTH_JWT_SECRET_KEY')
    jwt_algorithm: str = 'HS256'

    # Почта. Пустой хост включает канал-заглушку: сервис, поднятый локально без
    # почтового сервера, работает и складывает письма в журнал.
    smtp_host: str = ''
    smtp_port: int = 25
    smtp_use_tls: bool = False
    smtp_user: str = ''
    smtp_password: SecretStr = SecretStr('')
    smtp_from: str = 'Practix <noreply@practix.local>'
    # Установка SMTP-соединения занимает секунды, отправка письма — доли
    # секунды (урок «Как посылать быстрее»), поэтому соединения переиспользуются.
    smtp_pool_size: int = Field(default=4, ge=1)
    smtp_timeout: float = Field(default=30.0, gt=0)
    # Предел темпа отправки на воркер: внешний почтовый сервер положить
    # проще, чем свой сервис. 0 снимает ограничение.
    smtp_rate_per_second: float = Field(default=20.0, ge=0)

    # Пределы сборки письма из шаблона: шаблон пишет менеджер, и тяжёлый шаблон
    # не должен останавливать API и воркер. Время — на одно письмо, память —
    # на весь процесс сборки (на Linux).
    render_timeout: float = Field(default=2.0, gt=0)
    render_memory_limit_mb: int = Field(default=256, ge=0)

    # Ретранслятор outbox: как часто заглядывать в базу, сколько заданий брать
    # за раз и на сколько их откладывать, пока идёт публикация. Аренда — с
    # запасом над таймаутом брокера: задание не должно вернуться к другому
    # ретранслятору, пока первый ещё ждёт подтверждения.
    outbox_poll_interval: float = Field(default=0.5, gt=0)
    outbox_batch_size: int = Field(default=100, ge=1)
    outbox_lease: Duration = timedelta(seconds=30)

    # Пачка получателей в одном сообщении очереди. Тысяча — столько же, сколько
    # контактов отдаёт за раз справочник сервиса авторизации.
    batch_size: int = Field(default=1000, ge=1)
    # Сколько раз повторять обработку сообщения, прежде чем отправить его в
    # очередь разбора. Повтор — через отложенную очередь, а не сразу: смысла
    # биться в упавший сервис в цикле нет.
    max_attempts: int = Field(default=5, ge=1)
    retry_delay: Duration = timedelta(minutes=1)
    # Сколько отправитель держит письмо. Больше таймаута SMTP с запасом на
    # ожидание темпа: аренда не должна выйти посреди живой отправки. И меньше
    # суммы повторов (`max_attempts` × `retry_delay`): письмо брошенного
    # отправителя должно успеть вернуться, пока попытки не кончились.
    send_lease: Duration = timedelta(minutes=2)

    # Окно, в которое разрешено писать зрителю, в его собственном часовом
    # поясе. Сообщение, рождённое ночью, ждёт утра, а не будит человека.
    quiet_hours_start: int = Field(default=21, ge=0, le=23)
    quiet_hours_end: int = Field(default=9, ge=0, le=23)
    # Часовой пояс для тех, кто свой не указал.
    default_timezone: str = 'Europe/Moscow'
    # Единая политика контактов: больше этого числа писем в сутки зритель не
    # получит. Лишнее откладывается на завтра, а не теряется.
    max_messages_per_day: int = Field(default=5, ge=1)

    # Адрес websocket-шлюза для мгновенных сообщений в открытую вкладку.
    # Пустой выключает канал: стек поднимается и без него.
    websocket_url: str = ''
    # Сколько шлюз ждёт от зрителя признаков жизни, прежде чем закрыть
    # соединение. Без этого мёртвые соединения копятся до конца света.
    websocket_ping_interval: float = Field(default=20.0, gt=0)

    # Базовый адрес кинотеатра: из него собираются ссылки в письмах.
    public_base_url: str = 'http://localhost'
    # Сколько живёт ссылка подтверждения почты: и короткая ссылка, и токен в ней.
    confirm_link_ttl: Duration = timedelta(days=3)
    # Куда вести зрителя после подтверждения. Пустое значение — на главную
    # кинотеатра по `public_base_url`.
    confirm_redirect_url: str = ''

    # Идентификатор запроса ставит nginx, поэтому его отсутствие значит, что
    # запрос пришёл мимо шлюза, — и по умолчанию такой запрос отклоняется.
    require_request_id: bool = True

    sentry_dsn: str = ''
    sentry_environment: str = 'local'
    otlp_endpoint: str = ''

    @property
    def render_memory_limit(self) -> int:
        return self.render_memory_limit_mb * 1024 * 1024

    @property
    def confirm_redirect(self) -> str:
        return self.confirm_redirect_url or f'{self.public_base_url.rstrip("/")}/'

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
