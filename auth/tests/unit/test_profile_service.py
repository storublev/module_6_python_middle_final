"""Личный кабинет: смена логина и пароля, история входов."""

from urllib.parse import parse_qs, urlparse

import pytest

from models.user import User
from services.auth import AuthService, Principal, RegistrationService
from services.errors import (
    InvalidCredentialsError,
    LoginTakenError,
    TokenRevokedError,
    TooManyRequestsError,
    WrongPasswordError,
)
from services.passwords import PasswordHasher
from services.profile import Pagination, ProfileService
from services.tokens import TokenType
from tests.unit.conftest import CLIENT, PASSWORD, POLICY
from tests.unit.fakes import Database, FakeRateLimiter, FakeSessionStore, FakeUserRepository


@pytest.fixture
async def user(registration: RegistrationService) -> User:
    return await registration.register('neo', PASSWORD)


@pytest.fixture
async def principal(auth: AuthService, user: User) -> Principal:
    return await auth.authenticate((await auth.login('neo', PASSWORD, CLIENT)).access_token)


async def test_change_login(profiles: ProfileService, auth: AuthService, principal: Principal) -> None:
    """После смены логина войти можно только с новым логином."""
    user = await profiles.change_login(principal, 'theone', PASSWORD, CLIENT)

    assert user.login == 'theone'
    await auth.login('theone', PASSWORD, CLIENT)
    with pytest.raises(InvalidCredentialsError):
        await auth.login('neo', PASSWORD, CLIENT)


async def test_change_login_to_same_login(profiles: ProfileService, principal: Principal) -> None:
    """Смена логина на тот же самый — не ошибка «логин занят»."""
    user = await profiles.change_login(principal, 'neo', PASSWORD, CLIENT)

    assert user.login == 'neo'


async def test_change_login_to_taken_login_raises(
    profiles: ProfileService, registration: RegistrationService, principal: Principal,
) -> None:
    """Логин другого пользователя занять нельзя."""
    await registration.register('trinity', PASSWORD)

    with pytest.raises(LoginTakenError):
        await profiles.change_login(principal, 'trinity', PASSWORD, CLIENT)


async def test_change_login_requires_current_password(profiles: ProfileService, principal: Principal) -> None:
    """Без верного текущего пароля логин не меняется."""
    with pytest.raises(WrongPasswordError):
        await profiles.change_login(principal, 'theone', 'wrong-password', CLIENT)


async def test_change_password_revokes_other_sessions(
    profiles: ProfileService, auth: AuthService, principal: Principal,
) -> None:
    """Новый пароль действует сразу, старый — нет; остальные сессии закрываются, текущая остаётся."""
    other = await auth.login('neo', PASSWORD, CLIENT)

    await profiles.change_password(principal, PASSWORD, 'new-password', CLIENT)

    await auth.login('neo', 'new-password', CLIENT)
    with pytest.raises(InvalidCredentialsError):
        await auth.login('neo', PASSWORD, CLIENT)
    with pytest.raises(TokenRevokedError):
        await auth.authenticate(other.access_token)
    assert (await profiles.get_profile(principal)).user.login == 'neo'


async def test_change_password_revokes_sessions_when_redis_fails(
    profiles: ProfileService, auth: AuthService, principal: Principal, sessions: FakeSessionStore,
) -> None:
    """Redis недоступен после смены пароля: сессии остались в хранилище, но ни одна не действует.

    Пароль сменён — повторять запрос со старым паролем не нужно; войти заново
    придётся и на текущем устройстве, но уже с новым паролем.
    """
    other = await auth.login('neo', PASSWORD, CLIENT)
    sessions.writes_fail = True

    await profiles.change_password(principal, PASSWORD, 'new-password', CLIENT)

    sessions.writes_fail = False
    assert len(sessions.sessions) == 2
    with pytest.raises(TokenRevokedError):
        await auth.authenticate(other.access_token)
    with pytest.raises(TokenRevokedError):
        await auth.refresh(other.refresh_token)
    await auth.login('neo', 'new-password', CLIENT)


async def test_session_opened_before_password_change_is_revoked(
    auth: AuthService, users: FakeUserRepository, user: User,
) -> None:
    """Сессия с устаревшей версией учётных данных не действует, даже если её не удалили."""
    pair = await auth.login('neo', PASSWORD, CLIENT)

    await users.update_password(user.id, user.password_hash)

    with pytest.raises(TokenRevokedError):
        await auth.authenticate(pair.access_token)


async def test_session_of_deleted_user_is_revoked(auth: AuthService, db: Database, user: User) -> None:
    """Токены удалённого пользователя не действуют, хотя сессия ещё жива."""
    pair = await auth.login('neo', PASSWORD, CLIENT)

    del db.users[user.id]

    with pytest.raises(TokenRevokedError):
        await auth.authenticate(pair.access_token)


async def test_change_password_requires_current_password(profiles: ProfileService, principal: Principal) -> None:
    """Без верного текущего пароля пароль не меняется."""
    with pytest.raises(WrongPasswordError):
        await profiles.change_password(principal, 'wrong-password', 'new-password', CLIENT)


# Лимит проверок пароля

async def test_password_guessing_in_profile_is_limited(profiles: ProfileService, principal: Principal) -> None:
    """Неверные пароли в личном кабинете кончаются лимитом, а не бесконечным перебором."""
    for _ in range(POLICY.password_check_per_account.attempts):
        with pytest.raises(WrongPasswordError):
            await profiles.change_password(principal, 'wrong-password', 'new-password', CLIENT)

    with pytest.raises(TooManyRequestsError):
        await profiles.change_password(principal, 'wrong-password', 'new-password', CLIENT)


async def test_password_is_not_checked_over_the_limit(
    profiles: ProfileService, principal: Principal, passwords: PasswordHasher,
) -> None:
    """Сверх лимита пароль не проверяется: даже верный не пройдёт, и Argon2 не считается."""
    for _ in range(POLICY.password_check_per_account.attempts):
        with pytest.raises(WrongPasswordError):
            await profiles.change_password(principal, 'wrong-password', 'new-password', CLIENT)

    with pytest.raises(TooManyRequestsError):
        await profiles.change_password(principal, PASSWORD, 'new-password', CLIENT)


async def test_login_change_shares_the_same_limit(profiles: ProfileService, principal: Principal) -> None:
    """Смена логина проверяет пароль тем же лимитом: перебирать по очереди в двух местах нельзя."""
    for _ in range(POLICY.password_check_per_account.attempts):
        with pytest.raises(WrongPasswordError):
            await profiles.change_login(principal, 'theone', 'wrong-password', CLIENT)

    with pytest.raises(TooManyRequestsError):
        await profiles.change_password(principal, 'wrong-password', 'new-password', CLIENT)


async def test_correct_password_resets_the_limit(profiles: ProfileService, principal: Principal) -> None:
    """Верный пароль обнуляет счётчик: у владельца копятся только промахи."""
    with pytest.raises(WrongPasswordError):
        await profiles.change_login(principal, 'theone', 'wrong-password', CLIENT)
    await profiles.change_login(principal, 'theone', PASSWORD, CLIENT)

    with pytest.raises(WrongPasswordError):
        await profiles.change_login(principal, 'neo', 'wrong-password', CLIENT)


async def test_limit_is_counted_per_user_and_ip(
    profiles: ProfileService, registration: RegistrationService, auth: AuthService, limiter: FakeRateLimiter,
) -> None:
    """Попытки считаются и по учётной записи, и по адресу: смена аккаунта лимит не обнуляет."""
    await registration.register('trinity', PASSWORD)
    principal = await auth.authenticate((await auth.login('trinity', PASSWORD, CLIENT)).access_token)

    with pytest.raises(WrongPasswordError):
        await profiles.change_login(principal, 'theone', 'wrong-password', CLIENT)

    assert limiter.attempts[f'password:user:{principal.user_id}'] == 1
    assert limiter.attempts[f'password:ip:{CLIENT.ip}'] == 1


async def test_first_password_of_social_user_needs_no_attempt(
    profiles: ProfileService, social, auth: AuthService, tokens, limiter: FakeRateLimiter,
) -> None:
    """Первый пароль пользователя из соцсети подтверждать нечем, и попытка не тратится."""
    url = await social.start('yandex', redirect_uri='http://localhost/callback')
    state = parse_qs(urlparse(url).query)['state'][0]
    result = await social.complete('yandex', code='code', state=state, redirect_uri='http://localhost/callback',
                                   client=CLIENT)
    claims = tokens.decode(result.tokens.access_token, TokenType.ACCESS)
    principal = Principal(user_id=claims.user_id, session_id=claims.session_id)

    await profiles.change_password(principal, None, 'new-password', CLIENT)

    assert f'password:user:{principal.user_id}' not in limiter.attempts


async def test_login_history_newest_first_with_pages(
    profiles: ProfileService, auth: AuthService, principal: Principal,
) -> None:
    """История входов — от новых к старым, постранично."""
    for number in range(4):
        await auth.login('neo', PASSWORD, CLIENT.__class__(user_agent=f'device-{number}', ip=None))

    first_page = await profiles.login_history(principal, Pagination(page_number=1, page_size=3))
    second_page = await profiles.login_history(principal, Pagination(page_number=2, page_size=3))

    # Первый вход сделала фикстура principal с User-Agent pytest.
    assert [record.user_agent for record in first_page] == ['device-3', 'device-2', 'device-1']
    assert [record.user_agent for record in second_page] == ['device-0', 'pytest']


async def test_profile_of_deleted_user_is_revoked(profiles: ProfileService, principal: Principal, db) -> None:
    """Если учётную запись удалили, её ещё живая сессия не даёт доступа к кабинету."""
    db.users.clear()

    with pytest.raises(TokenRevokedError):
        await profiles.get_profile(principal)
