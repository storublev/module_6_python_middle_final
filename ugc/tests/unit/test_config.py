"""Настройки сервиса: префикс переменных и обязательные секреты."""

import pytest
from pydantic import ValidationError

from core.config import Settings
from tests.unit import SECRET_KEY


def test_variables_are_read_with_the_ugc_prefix(monkeypatch) -> None:
    """У переменных сервиса префикс UGC_: .env общий для всех сервисов кинотеатра."""
    monkeypatch.setenv('UGC_KAFKA_TOPIC', 'ugc.events.test')

    assert Settings().kafka_topic == 'ugc.events.test'


def test_jwt_secret_is_shared_with_the_auth_service(monkeypatch) -> None:
    """Ключ подписи берётся из AUTH_JWT_SECRET_KEY: подпись проверяется тем же секретом, которым выпущена."""
    monkeypatch.setenv('AUTH_JWT_SECRET_KEY', SECRET_KEY)

    assert Settings().jwt_secret_key.get_secret_value() == SECRET_KEY


def test_service_does_not_start_without_the_signing_key(monkeypatch) -> None:
    """Без ключа подписи сервис не стартует, а не работает с общеизвестным."""
    monkeypatch.delenv('AUTH_JWT_SECRET_KEY', raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_short_signing_key_is_rejected(monkeypatch) -> None:
    """Ключ короче 32 байт не принимается: для HS256 он подбирается быстрее, чем стоило бы."""
    monkeypatch.setenv('AUTH_JWT_SECRET_KEY', 'слишком короткий')

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_tracing_is_off_by_default() -> None:
    """Без адреса коллектора трассировка выключена: сервис запускается и без Jaeger."""
    assert Settings().otlp_endpoint == ''
