"""Токены, которые клиент не может получить от сервиса: истёкшие и поддельные."""

from datetime import UTC, datetime, timedelta

import jwt

from tests.functional.settings import settings

FORGED_KEY = 'someone-else-secret-key-of-32-bytes!'


def resign(token: str, key: str = settings.jwt_secret_key, **changes) -> str:
    """Тот же токен с изменёнными полями, подписанный ключом key."""
    payload = jwt.decode(token, options={'verify_signature': False})
    return jwt.encode({**payload, **changes}, key, algorithm='HS256')


def expired(token: str) -> str:
    """Токен с верной подписью, срок которого истёк минуту назад."""
    past = datetime.now(UTC) - timedelta(minutes=1)
    return resign(token, iat=past - timedelta(minutes=15), exp=past)


def forged(token: str) -> str:
    """Токен с теми же данными, подписанный чужим ключом."""
    return resign(token, key=FORGED_KEY)
