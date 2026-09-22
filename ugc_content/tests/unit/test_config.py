"""Настройки сервиса: префикс, секрет и пределы."""

import pytest
from pydantic import ValidationError

from core.config import Settings
from tests.unit import SECRET_KEY


def test_settings_read_prefixed_variables(monkeypatch):
    """Свои настройки сервис берёт из переменных с префиксом CONTENT_."""
    monkeypatch.setenv('CONTENT_MONGO_DATABASE', 'content-test')

    assert Settings().mongo_database == 'content-test'


def test_secret_key_is_shared_with_auth(monkeypatch):
    """Ключ подписи берётся без префикса: он общий с сервисом авторизации."""
    monkeypatch.setenv('AUTH_JWT_SECRET_KEY', SECRET_KEY)

    assert Settings().jwt_secret_key.get_secret_value() == SECRET_KEY


def test_short_secret_key_is_rejected(monkeypatch):
    """Короткий ключ HS256 не принимается: его подбирают быстрее, чем стоило бы."""
    monkeypatch.setenv('AUTH_JWT_SECRET_KEY', 'too-short')

    with pytest.raises(ValidationError):
        Settings()


def test_secret_key_is_not_printed():
    """Секрет не попадает в строковое представление настроек и, значит, в журнал."""
    assert SECRET_KEY not in str(Settings())
