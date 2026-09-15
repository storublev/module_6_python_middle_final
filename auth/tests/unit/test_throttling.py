"""Ограничение частоты входа и регистрации: лимиты по IP и логину, 429 до проверки пароля."""

import pytest

from models.user import User
from services.auth import AuthService, ClientInfo, RegistrationService, SignupService
from services.errors import InvalidCredentialsError, TooManyRequestsError
from services.passwords import PasswordHasher
from tests.unit.conftest import CLIENT, PASSWORD, POLICY

OTHER_CLIENT = ClientInfo(user_agent='pytest', ip='10.0.0.2')


def client(number: int) -> ClientInfo:
    return ClientInfo(user_agent='pytest', ip=f'10.1.0.{number}')


@pytest.fixture
async def user(registration: RegistrationService) -> User:
    return await registration.register('neo', PASSWORD)


@pytest.fixture
def verifications(passwords: PasswordHasher, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Пароли, которые сверялись с хешем, — и настоящим, и заглушкой."""
    checked: list[str] = []
    verify = passwords.verify

    async def counting_verify(password: str, password_hash: str) -> bool:
        checked.append(password)
        return await verify(password, password_hash)

    monkeypatch.setattr(passwords, 'verify', counting_verify)
    return checked


async def test_login_limit_per_ip(auth: AuthService, user: User, verifications: list[str]) -> None:
    """Сверх лимита адреса вход отклоняется до проверки пароля — даже с верным паролем; другой адрес входит."""
    for number in range(POLICY.login_per_ip.attempts):
        with pytest.raises(InvalidCredentialsError):
            await auth.login(f'nobody{number}', PASSWORD, CLIENT)
    checked = len(verifications)

    with pytest.raises(TooManyRequestsError) as error:
        await auth.login('neo', PASSWORD, CLIENT)

    assert error.value.retry_after == POLICY.login_per_ip.period.total_seconds()
    assert len(verifications) == checked
    await auth.login('neo', PASSWORD, OTHER_CLIENT)


async def test_login_limit_per_account(auth: AuthService, registration: RegistrationService, user: User) -> None:
    """Перебор пароля одного логина с разных адресов упирается в лимит логина; другие логины входят."""
    await registration.register('trinity', PASSWORD)
    for number in range(POLICY.login_per_account.attempts):
        with pytest.raises(InvalidCredentialsError):
            await auth.login('neo', 'wrong-password', client(number))

    with pytest.raises(TooManyRequestsError):
        await auth.login('neo', PASSWORD, client(100))
    await auth.login('trinity', PASSWORD, client(100))


async def test_successful_login_resets_account_limit(auth: AuthService, user: User) -> None:
    """Успешный вход обнуляет счётчик логина: неудачные попытки владельца не копятся бесконечно."""
    for _ in range(2):
        for _ in range(POLICY.login_per_account.attempts - 1):
            with pytest.raises(InvalidCredentialsError):
                await auth.login('neo', 'wrong-password', client(1))
        await auth.login('neo', PASSWORD, client(2))


async def test_unknown_login_attempts_are_limited(auth: AuthService, verifications: list[str]) -> None:
    """Вход с несуществующим логином тоже считается: хеш-заглушку сверх лимита не посчитать."""
    for number in range(POLICY.login_per_account.attempts):
        with pytest.raises(InvalidCredentialsError):
            await auth.login('nobody', PASSWORD, client(number))

    with pytest.raises(TooManyRequestsError):
        await auth.login('nobody', PASSWORD, client(100))
    assert len(verifications) == POLICY.login_per_account.attempts


async def test_signup_limit_per_ip(signups: SignupService) -> None:
    """Сверх лимита регистраций с адреса — TooManyRequestsError; с другого адреса регистрация проходит."""
    for number in range(POLICY.signup_per_ip.attempts):
        await signups.signup(f'user{number}', PASSWORD, CLIENT)

    with pytest.raises(TooManyRequestsError) as error:
        await signups.signup('late', PASSWORD, CLIENT)

    assert error.value.retry_after == POLICY.signup_per_ip.period.total_seconds()
    await signups.signup('late', PASSWORD, OTHER_CLIENT)


async def test_signup_and_login_limits_are_separate(auth: AuthService, signups: SignupService, user: User) -> None:
    """Исчерпанный лимит регистраций не мешает входить с того же адреса."""
    for number in range(POLICY.signup_per_ip.attempts):
        await signups.signup(f'user{number}', PASSWORD, CLIENT)

    await auth.login('neo', PASSWORD, CLIENT)
