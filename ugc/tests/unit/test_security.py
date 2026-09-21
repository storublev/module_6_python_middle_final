"""Опознание пользователя по access-токену сервиса авторизации."""

from datetime import timedelta
from http import HTTPStatus
from uuid import uuid4

import pytest

from api.errors import ApiError
from api.security import TokenVerifier
from tests.unit import SECRET_KEY


@pytest.fixture
def verifier() -> TokenVerifier:
    return TokenVerifier(SECRET_KEY, 'HS256')


def test_valid_token_gives_the_user(verifier, make_token) -> None:
    """Из действующего access-токена достаётся пользователь и его сессия."""
    user_id = uuid4()

    user = verifier.verify(f'Bearer {make_token(user_id)}')

    assert user.user_id == user_id


def test_missing_header_is_rejected(verifier) -> None:
    """Без заголовка Authorization — 401 not_authenticated."""
    with pytest.raises(ApiError) as error:
        verifier.verify(None)

    assert error.value.status == HTTPStatus.UNAUTHORIZED
    assert error.value.code == 'not_authenticated'


def test_wrong_scheme_is_rejected(verifier, make_token) -> None:
    """Схема, отличная от Bearer, не принимается."""
    with pytest.raises(ApiError) as error:
        verifier.verify(f'Basic {make_token()}')

    assert error.value.code == 'not_authenticated'


def test_empty_token_is_rejected(verifier) -> None:
    """Заголовок без самого токена не принимается."""
    with pytest.raises(ApiError) as error:
        verifier.verify('Bearer ')

    assert error.value.code == 'not_authenticated'


def test_expired_token_is_rejected(verifier, make_token) -> None:
    """Истёкший токен отличается от неверного: клиенту нужно обновить пару, а не входить заново."""
    with pytest.raises(ApiError) as error:
        verifier.verify(f'Bearer {make_token(ttl=timedelta(minutes=-1))}')

    assert error.value.code == 'token_expired'


def test_token_signed_with_another_key_is_rejected(verifier, make_token) -> None:
    """Токен, подписанный чужим ключом, не принимается."""
    with pytest.raises(ApiError) as error:
        verifier.verify(f'Bearer {make_token(secret="another-secret-key-of-at-least-32-bytes")}')

    assert error.value.code == 'token_invalid'


def test_refresh_token_is_not_accepted(verifier, make_token) -> None:
    """Refresh-токеном события слать нельзя: он живёт две недели."""
    with pytest.raises(ApiError) as error:
        verifier.verify(f'Bearer {make_token(token_type="refresh")}')

    assert error.value.code == 'token_invalid'


def test_token_without_expiration_is_rejected(verifier, make_token) -> None:
    """Токен без exp не принимается: иначе он оказался бы вечным."""
    with pytest.raises(ApiError) as error:
        verifier.verify(f'Bearer {make_token(drop="exp")}')

    assert error.value.code == 'token_invalid'


def test_malformed_token_is_rejected(verifier) -> None:
    """Испорченная строка вместо токена не принимается."""
    with pytest.raises(ApiError) as error:
        verifier.verify('Bearer not-a-token')

    assert error.value.code == 'token_invalid'


def test_token_with_broken_claims_is_rejected(verifier, make_token) -> None:
    """Токен с sub не в формате UUID не принимается."""
    with pytest.raises(ApiError) as error:
        verifier.verify(f'Bearer {make_token(sub="не-uuid")}')

    assert error.value.code == 'token_invalid'
