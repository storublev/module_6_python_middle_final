"""Выпуск и проверка JWT: подпись, срок, тип и состав токена."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from services.errors import TokenExpiredError, TokenInvalidError
from services.tokens import TokenService, TokenType
from tests.unit.conftest import SECRET_KEY


def make_service(clock: datetime | None = None) -> TokenService:
    return TokenService(
        SECRET_KEY, 'HS256', timedelta(minutes=15), timedelta(days=14),
        clock=(lambda: clock) if clock else (lambda: datetime.now(UTC)),
    )


def test_issued_tokens_decode_to_same_user_and_session(tokens: TokenService) -> None:
    """Оба токена пары указывают на пользователя и сессию, jti refresh-токена совпадает с отданным."""
    user_id, session_id = uuid4(), uuid4()
    pair = tokens.issue(user_id, session_id)

    access = tokens.decode(pair.access_token, TokenType.ACCESS)
    refresh = tokens.decode(pair.refresh_token, TokenType.REFRESH)

    assert (access.user_id, access.session_id) == (user_id, session_id)
    assert (refresh.user_id, refresh.session_id) == (user_id, session_id)
    assert refresh.jti == pair.refresh_jti
    assert access.jti != refresh.jti
    assert pair.expires_in == 15 * 60


def test_tokens_have_configured_lifetime(tokens: TokenService) -> None:
    """Access-токен живёт 15 минут, refresh-токен — 14 дней."""
    pair = tokens.issue(uuid4(), uuid4())

    for token, ttl in ((pair.access_token, timedelta(minutes=15)), (pair.refresh_token, timedelta(days=14))):
        claims = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        assert claims['exp'] - claims['iat'] == ttl.total_seconds()


def test_expired_token_raises_token_expired() -> None:
    """Токен с истёкшим сроком и верной подписью — token_expired: клиенту стоит обновить пару."""
    issued = make_service(clock=datetime.now(UTC) - timedelta(hours=1)).issue(uuid4(), uuid4())

    with pytest.raises(TokenExpiredError):
        make_service().decode(issued.access_token, TokenType.ACCESS)


def test_token_signed_with_other_key_is_invalid(tokens: TokenService) -> None:
    """Токен, подписанный чужим ключом, — token_invalid."""
    forged = TokenService('another-secret-key-of-at-least-32-bytes', 'HS256', timedelta(minutes=15),
                          timedelta(days=14)).issue(uuid4(), uuid4())

    with pytest.raises(TokenInvalidError, match='signature'):
        tokens.decode(forged.access_token, TokenType.ACCESS)


def test_forged_expired_token_is_invalid_not_expired(tokens: TokenService) -> None:
    """Подпись проверяется раньше срока: поддельный истёкший токен — token_invalid, а не token_expired."""
    forger = TokenService('another-secret-key-of-at-least-32-bytes', 'HS256', timedelta(minutes=15),
                          timedelta(days=14), clock=lambda: datetime.now(UTC) - timedelta(hours=1))

    with pytest.raises(TokenInvalidError):
        tokens.decode(forger.issue(uuid4(), uuid4()).access_token, TokenType.ACCESS)


def test_unsigned_token_is_invalid(tokens: TokenService) -> None:
    """Токен с alg=none не принимается, даже если в нём все нужные поля."""
    payload = jwt.decode(tokens.issue(uuid4(), uuid4()).access_token, options={'verify_signature': False})
    unsigned = jwt.encode(payload, key=None, algorithm='none')

    with pytest.raises(TokenInvalidError):
        tokens.decode(unsigned, TokenType.ACCESS)


@pytest.mark.parametrize('expected, actual', [(TokenType.ACCESS, 'refresh'), (TokenType.REFRESH, 'access')])
def test_token_of_other_type_is_invalid(tokens: TokenService, expected: TokenType, actual: str) -> None:
    """refresh-токен нельзя использовать вместо access-токена и наоборот."""
    pair = tokens.issue(uuid4(), uuid4())
    token = pair.refresh_token if actual == 'refresh' else pair.access_token

    with pytest.raises(TokenInvalidError, match='expected'):
        tokens.decode(token, expected)


@pytest.mark.parametrize('token', ['', 'not-a-jwt', 'a.b.c'])
def test_malformed_token_is_invalid(tokens: TokenService, token: str) -> None:
    """Строка, не являющаяся JWT, — token_invalid."""
    with pytest.raises(TokenInvalidError):
        tokens.decode(token, TokenType.ACCESS)


def test_token_without_session_claim_is_invalid(tokens: TokenService) -> None:
    """Подписанный нашим ключом токен без обязательного поля sid не принимается."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {'sub': str(uuid4()), 'jti': 'x', 'type': 'access', 'iat': now, 'exp': now + timedelta(minutes=5)},
        SECRET_KEY, algorithm='HS256',
    )

    with pytest.raises(TokenInvalidError):
        tokens.decode(token, TokenType.ACCESS)


def test_token_with_malformed_user_id_is_invalid(tokens: TokenService) -> None:
    """Подписанный нашим ключом токен с sub не в формате UUID не принимается."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {'sub': 'neo', 'sid': str(uuid4()), 'jti': 'x', 'type': 'access', 'iat': now,
         'exp': now + timedelta(minutes=5)},
        SECRET_KEY, algorithm='HS256',
    )

    with pytest.raises(TokenInvalidError, match='claims'):
        tokens.decode(token, TokenType.ACCESS)
