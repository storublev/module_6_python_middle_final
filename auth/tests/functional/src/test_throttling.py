"""Ограничение частоты входа, регистрации и проверок пароля: 429 по IP, логину и учётной записи.

Адрес клиента сервис берёт из X-Forwarded-For (в работе его выставляет
nginx), поэтому тесты «приходят с разных IP» через этот заголовок.
"""

import asyncpg
import httpx

from tests.functional.conftest import PASSWORD, Account, MakeAccount, signup
from tests.functional.settings import settings


def from_ip(ip: str) -> dict[str, str]:
    return {'X-Forwarded-For': ip}


async def attempt_login(client: httpx.AsyncClient, login: str, password: str, ip: str) -> httpx.Response:
    return await client.post('/login', json={'login': login, 'password': password}, headers=from_ip(ip))


def assert_too_many_requests(response: httpx.Response) -> None:
    assert response.status_code == 429
    assert response.json()['code'] == 'too_many_requests'
    assert int(response.headers['retry-after']) > 0


async def test_login_limit_per_ip(client: httpx.AsyncClient, make_account: MakeAccount) -> None:
    """429 после лимита попыток с одного IP — даже с верным паролем; с другого IP вход проходит."""
    await make_account('neo')
    for number in range(settings.login_attempts_per_ip):
        response = await attempt_login(client, f'nobody{number}', PASSWORD, '203.0.113.1')
        assert response.status_code == 401

    assert_too_many_requests(await attempt_login(client, 'neo', PASSWORD, '203.0.113.1'))
    assert (await attempt_login(client, 'neo', PASSWORD, '203.0.113.2')).status_code == 200


async def test_login_limit_per_account(client: httpx.AsyncClient, make_account: MakeAccount) -> None:
    """429 после лимита неудачных попыток для логина с разных IP; другой логин с того же IP входит."""
    await make_account('neo')
    await make_account('trinity')
    for number in range(settings.login_attempts_per_account):
        response = await attempt_login(client, 'neo', 'wrong-password', f'198.51.100.{number}')
        assert response.status_code == 401

    assert_too_many_requests(await attempt_login(client, 'neo', PASSWORD, '198.51.100.200'))
    assert (await attempt_login(client, 'trinity', PASSWORD, '198.51.100.200')).status_code == 200


async def test_successful_login_resets_account_limit(client: httpx.AsyncClient, make_account: MakeAccount) -> None:
    """Успешный вход обнуляет счётчик логина: владелец, изредка ошибаясь, не упирается в лимит."""
    await make_account('neo')
    for round_number in range(2):
        for number in range(settings.login_attempts_per_account - 1):
            ip = f'192.0.2.{round_number * 10 + number}'
            assert (await attempt_login(client, 'neo', 'wrong-password', ip)).status_code == 401
        assert (await attempt_login(client, 'neo', PASSWORD, '192.0.2.100')).status_code == 200


async def test_throttled_login_is_not_recorded(
    client: httpx.AsyncClient, make_account: MakeAccount, pg: asyncpg.Connection,
) -> None:
    """Отклонённая по лимиту попытка не открывает сессию и не попадает в историю входов."""
    neo = await make_account('neo')
    for _ in range(settings.login_attempts_per_account):
        await attempt_login(client, 'neo', 'wrong-password', '203.0.113.5')

    assert_too_many_requests(await attempt_login(client, 'neo', PASSWORD, '203.0.113.6'))

    assert await pg.fetchval('SELECT count(*) FROM auth.login_history WHERE user_id = $1', neo.id) == 1


async def test_signup_limit_per_ip(client: httpx.AsyncClient) -> None:
    """429 после лимита регистраций с одного IP; с другого IP регистрация проходит."""
    for number in range(settings.signup_attempts_per_ip):
        response = await client.post('/signup', json={'login': f'user{number}', 'password': PASSWORD},
                                     headers=from_ip('203.0.113.10'))
        assert response.status_code == 201

    response = await client.post('/signup', json={'login': 'late', 'password': PASSWORD},
                                 headers=from_ip('203.0.113.10'))
    assert_too_many_requests(response)

    response = await client.post('/signup', json={'login': 'late', 'password': PASSWORD},
                                 headers=from_ip('203.0.113.11'))
    assert response.status_code == 201


async def attempt_password_change(
    client: httpx.AsyncClient, account: Account, password: str, ip: str,
) -> httpx.Response:
    """Смена пароля в личном кабинете с подтверждением паролем `password`."""
    return await client.put(
        '/users/me/password',
        json={'password': password, 'new_password': 'newpassword123'},
        headers={**account.headers, **from_ip(ip)},
    )


async def test_password_check_limit_per_account(client: httpx.AsyncClient, make_account: MakeAccount) -> None:
    """429 после лимита неверных паролей в кабинете: перебирать пароль там нельзя."""
    neo = await make_account('neo')
    for number in range(settings.password_check_attempts_per_account):
        response = await attempt_password_change(client, neo, 'wrong-password', f'203.0.113.{number + 30}')
        assert response.status_code == 403, response.text

    assert_too_many_requests(await attempt_password_change(client, neo, PASSWORD, '203.0.113.40'))


async def test_password_check_limit_per_ip(
    client: httpx.AsyncClient, make_account: MakeAccount,
) -> None:
    """429 после лимита неверных паролей с одного IP, даже если аккаунты разные."""
    ip = '203.0.113.50'
    for number in range(settings.password_check_attempts_per_ip):
        account = await make_account(f'user{number}')
        response = await attempt_password_change(client, account, 'wrong-password', ip)
        assert response.status_code == 403, response.text

    late = await make_account('late')
    assert_too_many_requests(await attempt_password_change(client, late, PASSWORD, ip))


async def test_password_check_limit_covers_login_change(
    client: httpx.AsyncClient, make_account: MakeAccount,
) -> None:
    """Смена логина считается тем же лимитом: перебирать пароль по очереди в двух местах нельзя."""
    neo = await make_account('neo')
    for number in range(settings.password_check_attempts_per_account):
        response = await client.patch(
            '/users/me/login',
            json={'new_login': f'theone{number}', 'password': 'wrong-password'},
            headers={**neo.headers, **from_ip(f'203.0.113.{number + 60}')},
        )
        assert response.status_code == 403, response.text

    assert_too_many_requests(await attempt_password_change(client, neo, PASSWORD, '203.0.113.70'))


async def test_profile_limit_does_not_block_login(client: httpx.AsyncClient, make_account: MakeAccount) -> None:
    """Исчерпанный лимит кабинета не мешает войти по паролю с того же адреса."""
    ip = '203.0.113.80'
    neo = await make_account('neo')
    for _ in range(settings.password_check_attempts_per_account):
        await attempt_password_change(client, neo, 'wrong-password', ip)

    assert (await attempt_login(client, 'neo', PASSWORD, ip)).status_code == 200


async def test_signup_limit_does_not_block_login(client: httpx.AsyncClient) -> None:
    """Исчерпанный лимит регистраций не мешает входить с того же IP."""
    await signup(client, 'neo')
    for number in range(settings.signup_attempts_per_ip):
        await client.post('/signup', json={'login': f'user{number}', 'password': PASSWORD},
                          headers=from_ip('203.0.113.20'))

    assert (await attempt_login(client, 'neo', PASSWORD, '203.0.113.20')).status_code == 200
