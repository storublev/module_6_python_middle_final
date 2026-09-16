"""Настройки админки.

Значения берутся из переменных окружения с префиксом DJANGO_: файл .env общий
для всех сервисов кинотеатра, и без префикса POSTGRES_DB админки совпал бы с
переменными других сервисов. Проверяет и приводит их pydantic-settings — как в
сервисе авторизации; Django получает уже готовые константы.

Секрет подписи сессий значения по умолчанию не имеет: без него админка не
стартует, а не работает с общеизвестным ключом.
"""

from pathlib import Path
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


def split_commas(value: Any) -> Any:
    """Список строк из переменной окружения, перечисленной через запятую.

    pydantic-settings разбирает списки как JSON, а «localhost,127.0.0.1»
    читается человеком и привычнее в .env, поэтому разбор отключён (NoDecode),
    а строку делит этот валидатор.
    """
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return value


CommaSeparated = Annotated[list[str], NoDecode, BeforeValidator(split_commas)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='DJANGO_', env_file='.env', extra='ignore')

    secret_key: SecretStr = Field(min_length=32)
    debug: bool = False
    allowed_hosts: CommaSeparated = ['localhost', '127.0.0.1']
    # Админка отправляет формы, поэтому за прокси нужен список доверенных
    # источников: иначе Django отклонит POST с ошибкой проверки CSRF.
    csrf_trusted_origins: CommaSeparated = ['http://localhost', 'http://127.0.0.1']
    log_level: str = 'INFO'

    # База фильмов — общая с ETL и сервисом контента; схема content создаётся дампом.
    postgres_host: str = '127.0.0.1'
    postgres_port: int = 5432
    postgres_db: str = 'movies_database'
    postgres_user: str = 'app'
    postgres_password: SecretStr
    postgres_schema: str = 'content'

    # Сервис авторизации: вход сотрудников проверяется в нём, а не по локальным паролям.
    auth_api_url: str = 'http://auth:8000'
    # Право, дающее вход в админку. Его выдаёт роль staff (миграция 0005 сервиса авторизации).
    auth_admin_permission: str = 'admin.access'
    # Ждать ответа сервиса авторизации дольше нескольких секунд бессмысленно:
    # столько же ждёт человек у формы входа.
    auth_connect_timeout: float = 1.0
    auth_read_timeout: float = 3.0
    # Повторяется только обрыв соединения: запрос, на который сервис не ответил
    # за read_timeout, повторять нельзя — вход мог уже состояться.
    auth_connect_retries: int = 2
    auth_backoff_factor: float = 0.2
    # Прерыватель: после скольких сбоев подряд перестать ходить в сервис
    # авторизации и на сколько секунд.
    auth_breaker_failures: int = 5
    auth_breaker_reset_timeout: float = 30.0

    static_root: Path = BASE_DIR / 'staticfiles'
    static_url: str = '/static/'


settings = Settings()

# Django видит только настройки, записанные ЗАГЛАВНЫМИ буквами, поэтому
# разобранные pydantic значения доступны коду как settings.APP_SETTINGS —
# одним типизированным объектом, а не россыпью строк.
APP_SETTINGS = settings

SECRET_KEY = settings.secret_key.get_secret_value()
DEBUG = settings.debug
ALLOWED_HOSTS = settings.allowed_hosts
CSRF_TRUSTED_ORIGINS = settings.csrf_trusted_origins

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'movies.apps.MoviesConfig',
    'users.apps.UsersConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': settings.postgres_db,
        'USER': settings.postgres_user,
        'PASSWORD': settings.postgres_password.get_secret_value(),
        'HOST': settings.postgres_host,
        'PORT': settings.postgres_port,
        'OPTIONS': {
            # Таблицы каталога лежат в схеме content, созданной дампом ETL, а
            # собственные таблицы Django (сессии, журнал действий, сотрудники) —
            # в public: он первый в пути поиска, и миграции создают их там.
            'options': f'-c search_path=public,{settings.postgres_schema}',
        },
    },
}

AUTH_USER_MODEL = 'users.User'
# Первым идёт вход через сервис авторизации: логин и пароль сотрудника хранятся
# только там. ModelBackend оставлен вторым как аварийный вход: локальный
# суперпользователь (manage.py createsuperuser) войдёт, даже если сервис
# авторизации недоступен. У сотрудников из сервиса пароль в базе админки не
# хранится, поэтому ModelBackend их не пропустит.
AUTHENTICATION_BACKENDS = [
    'users.backends.AuthServiceBackend',
    'django.contrib.auth.backends.ModelBackend',
]

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / 'locale']

STATIC_URL = settings.static_url
STATIC_ROOT = settings.static_root

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# За nginx запрос приходит по HTTP, а снаружи может быть HTTPS.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'default'},
    },
    'root': {'handlers': ['console'], 'level': settings.log_level},
}
