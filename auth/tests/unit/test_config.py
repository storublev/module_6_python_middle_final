"""Настройки из переменных окружения: время жизни числом секунд или в ISO 8601."""

from datetime import timedelta

import pytest

from core.config import Settings


@pytest.mark.parametrize('value, expected', [
    ('900', timedelta(minutes=15)),
    (' 1.5 ', timedelta(seconds=1.5)),
    ('PT15M', timedelta(minutes=15)),
    ('00:15:00', timedelta(minutes=15)),
], ids=['seconds', 'fractional seconds', 'ISO 8601', 'hh:mm:ss'])
def test_duration_from_environment(monkeypatch: pytest.MonkeyPatch, value: str, expected: timedelta) -> None:
    """Время жизни в переменной окружения — число секунд или ISO 8601, как обещает README."""
    monkeypatch.setenv('AUTH_ACCESS_TOKEN_TTL', value)

    assert Settings().access_token_ttl == expected


def test_invalid_duration_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Непонятное значение времени жизни — ошибка при старте, а не молчаливое значение по умолчанию."""
    monkeypatch.setenv('AUTH_ACCESS_TOKEN_TTL', 'fifteen minutes')

    with pytest.raises(ValueError, match='access_token_ttl'):
        Settings()
