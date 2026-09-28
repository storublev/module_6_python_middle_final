"""Настройки сервиса: секреты, длительности и адрес базы."""

import pytest
from pydantic import ValidationError

from core.config import Settings
from tests.unit import SECRET_KEY

PASSWORD = 'pa55w0rd-of-postgres'
# Ключ подписи объявлен с именем переменной сервиса авторизации, поэтому и при
# создании объекта задаётся именно этим именем, а не именем поля.
REQUIRED: dict = {
    'postgres_password': PASSWORD,
    'rabbit_url': 'amqp://guest:guest@rabbit:5672/',
    'AUTH_JWT_SECRET_KEY': SECRET_KEY,
}


def build(**overrides: object) -> Settings:
    return Settings(**{**REQUIRED, **overrides})  # type: ignore[arg-type]


def test_short_jwt_key_is_rejected() -> None:
    """Ключ короче 32 байт не принимается: для HS256 он подбирается быстрее, чем стоило бы."""
    with pytest.raises(ValidationError):
        build(AUTH_JWT_SECRET_KEY='too-short')


def test_secrets_are_not_printed() -> None:
    """Секреты не попадают в вывод: из журнала их потом не вымыть."""
    settings = build()

    assert PASSWORD not in repr(settings)
    assert SECRET_KEY not in repr(settings)
    assert 'amqp://guest' not in repr(settings)


def test_duration_accepts_plain_seconds() -> None:
    """Длительность можно задать числом секунд — так удобнее в compose."""
    settings = build(retry_delay='30')

    assert settings.retry_delay.total_seconds() == 30


def test_postgres_dsn_is_assembled_from_parts() -> None:
    """Адрес базы собирается из частей и использует асинхронный драйвер."""
    settings = build(postgres_host='db', postgres_db='notify')

    assert settings.postgres_dsn.startswith('postgresql+asyncpg://')
    assert '@db:5432/notify' in settings.postgres_dsn


def test_jwt_key_comes_from_shared_variable() -> None:
    """Ключ подписи читается из общей переменной сервиса авторизации.

    Своей копии с другим именем у него быть не должно: подпись проверяется тем
    же секретом, которым выпущена.
    """
    settings = build()

    assert settings.jwt_secret_key.get_secret_value() == SECRET_KEY


def test_quiet_hours_are_limited_to_a_day() -> None:
    """Час окна тишины не может быть 25-м."""
    with pytest.raises(ValidationError):
        build(quiet_hours_start=25)


def test_environment_prefix_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """Переменные читаются с префиксом NOTIFY_.

    Без префикса `POSTGRES_DB` этого сервиса столкнулся бы с базой фильмов:
    .env у всех сервисов кинотеатра общий.
    """
    monkeypatch.setenv('NOTIFY_POSTGRES_DB', 'from-environment')

    assert build().postgres_db == 'from-environment'
