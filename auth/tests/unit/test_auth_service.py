"""Регистрация, вход, обновление токенов, выход и выход из остальных сессий."""

import pytest

from models.user import User
from services.auth import AuthService, RegistrationService
from services.errors import InvalidCredentialsError, LoginTakenError, TokenRevokedError
from services.passwords import PasswordHasher
from tests.unit.conftest import CLIENT, PASSWORD
from tests.unit.fakes import Database, FakeSessionStore


@pytest.fixture
async def user(registration: RegistrationService) -> User:
    return await registration.register('neo', PASSWORD)


async def test_register_stores_password_hash_not_password(registration: RegistrationService,
                                                          passwords: PasswordHasher) -> None:
    """В хранилище попадает хеш Argon2, по которому пароль проверяется, но не сам пароль."""
    user = await registration.register('neo', PASSWORD)

    assert user.password_hash != PASSWORD
    assert user.password_hash.startswith('$argon2id$')
    assert await passwords.verify(PASSWORD, user.password_hash)
    assert not user.is_superuser


async def test_register_taken_login_raises(registration: RegistrationService, user: User) -> None:
    """Повторная регистрация логина — LoginTakenError."""
    with pytest.raises(LoginTakenError):
        await registration.register('neo', PASSWORD)


async def test_register_superuser(registration: RegistrationService) -> None:
    """Консольная команда создаёт пользователя с признаком суперпользователя."""
    admin = await registration.register('admin', PASSWORD, is_superuser=True)

    assert admin.is_superuser


async def test_login_opens_session_and_records_history(
    auth: AuthService, user: User, sessions: FakeSessionStore, db: Database,
) -> None:
    """Вход открывает сессию с jti выданного refresh-токена и записывает устройство в историю."""
    pair = await auth.login('neo', PASSWORD, CLIENT)

    principal = await auth.authenticate(pair.access_token)
    assert principal.user_id == user.id
    assert sessions.sessions[principal.session_id].refresh_jti == pair.refresh_jti
    [(owner, record)] = db.history
    assert owner == user.id
    assert (record.user_agent, record.ip) == ('pytest', '127.0.0.1')


async def test_each_login_is_separate_session(auth: AuthService, user: User) -> None:
    """Каждый вход — отдельная сессия, как при входе с разных устройств."""
    first = await auth.authenticate((await auth.login('neo', PASSWORD, CLIENT)).access_token)
    second = await auth.authenticate((await auth.login('neo', PASSWORD, CLIENT)).access_token)

    assert first.session_id != second.session_id


@pytest.mark.parametrize('login, password', [('neo', 'wrong-password'), ('nobody', PASSWORD)])
async def test_login_with_invalid_credentials_raises(
    auth: AuthService, user: User, db: Database, login: str, password: str,
) -> None:
    """Неверный пароль и неизвестный логин — одна и та же ошибка, вход не записывается."""
    with pytest.raises(InvalidCredentialsError):
        await auth.login(login, password, CLIENT)
    assert db.history == []


async def test_long_user_agent_is_truncated(auth: AuthService, user: User, db: Database) -> None:
    """User-Agent длиннее 512 символов обрезается, чтобы поместиться в историю входов."""
    await auth.login('neo', PASSWORD, CLIENT.__class__(user_agent='x' * 1000, ip=None))

    assert len(db.history[0][1].user_agent) == 512


async def test_refresh_rotates_refresh_token(auth: AuthService, user: User, sessions: FakeSessionStore) -> None:
    """Обновление выдаёт новую пару в той же сессии, и сессия запоминает новый refresh-токен."""
    pair = await auth.login('neo', PASSWORD, CLIENT)

    new_pair = await auth.refresh(pair.refresh_token)

    principal = await auth.authenticate(new_pair.access_token)
    assert principal == await auth.authenticate(pair.access_token)
    assert sessions.sessions[principal.session_id].refresh_jti == new_pair.refresh_jti


async def test_reused_refresh_token_terminates_session(auth: AuthService, user: User) -> None:
    """Повторно предъявленный refresh-токен закрывает сессию: не действуют ни старая, ни новая пара."""
    pair = await auth.login('neo', PASSWORD, CLIENT)
    new_pair = await auth.refresh(pair.refresh_token)

    with pytest.raises(TokenRevokedError, match='already been used'):
        await auth.refresh(pair.refresh_token)

    with pytest.raises(TokenRevokedError):
        await auth.refresh(new_pair.refresh_token)
    with pytest.raises(TokenRevokedError):
        await auth.authenticate(new_pair.access_token)


async def test_logout_revokes_session_tokens(auth: AuthService, user: User) -> None:
    """После выхода не действуют ни access-, ни refresh-токен этой сессии."""
    pair = await auth.login('neo', PASSWORD, CLIENT)

    await auth.logout(await auth.authenticate(pair.access_token))

    with pytest.raises(TokenRevokedError):
        await auth.authenticate(pair.access_token)
    with pytest.raises(TokenRevokedError):
        await auth.refresh(pair.refresh_token)


async def test_logout_others_keeps_current_session(auth: AuthService, user: User) -> None:
    """«Выйти из остальных» закрывает другие сессии пользователя и оставляет текущую."""
    current = await auth.login('neo', PASSWORD, CLIENT)
    other = await auth.login('neo', PASSWORD, CLIENT)

    closed = await auth.logout_others(await auth.authenticate(current.access_token))

    assert closed == 1
    await auth.authenticate(current.access_token)
    with pytest.raises(TokenRevokedError):
        await auth.authenticate(other.access_token)
    with pytest.raises(TokenRevokedError):
        await auth.refresh(other.refresh_token)


async def test_logout_others_does_not_touch_other_users(
    auth: AuthService, registration: RegistrationService, user: User,
) -> None:
    """Сессии других пользователей «выйти из остальных» не закрывает."""
    await registration.register('trinity', PASSWORD)
    neo = await auth.login('neo', PASSWORD, CLIENT)
    trinity = await auth.login('trinity', PASSWORD, CLIENT)

    await auth.logout_others(await auth.authenticate(neo.access_token))

    await auth.authenticate(trinity.access_token)
