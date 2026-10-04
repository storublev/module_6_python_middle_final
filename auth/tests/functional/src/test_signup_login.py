"""Регистрация и вход."""

import asyncpg
import httpx
import jwt
import pytest

from tests.functional.conftest import PASSWORD, Account, login, signup

INVALID_SIGNUPS = {
    'short login': {'login': 'ab', 'password': PASSWORD},
    'long login': {'login': 'a' * 65, 'password': PASSWORD},
    'login with space': {'login': 'neo anderson', 'password': PASSWORD},
    'login with forbidden char': {'login': 'neo!', 'password': PASSWORD},
    'short password': {'login': 'neo', 'password': 'short'},
    'long password': {'login': 'neo', 'password': 'x' * 129},
    'no login': {'password': PASSWORD},
    'no password': {'login': 'neo'},
}


async def test_signup(client: httpx.AsyncClient, pg: asyncpg.Connection) -> None:
    """201: пользователь создан, логин в нижнем регистре, в ответе нет пароля, в базе — хеш Argon2."""
    response = await client.post('/signup', json={'login': '  Neo ', 'password': PASSWORD})

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {'id', 'login', 'created_at'}
    assert body['login'] == 'neo'
    password_hash = await pg.fetchval('SELECT password_hash FROM auth.users WHERE id = $1', body['id'])
    assert password_hash.startswith('$argon2id$')
    assert PASSWORD not in password_hash


@pytest.mark.parametrize('taken', ['neo', 'NEO'])
async def test_signup_login_taken(client: httpx.AsyncClient, taken: str) -> None:
    """409 login_taken, в том числе для логина в другом регистре."""
    await signup(client, 'neo')

    response = await client.post('/signup', json={'login': taken, 'password': PASSWORD})

    assert response.status_code == 409
    assert response.json() == {'code': 'login_taken', 'detail': 'Login is already taken'}


@pytest.mark.parametrize('body', INVALID_SIGNUPS.values(), ids=INVALID_SIGNUPS.keys())
async def test_signup_invalid(client: httpx.AsyncClient, body: dict) -> None:
    """422 на неверный логин или пароль; присланные значения в ответ не попадают."""
    response = await client.post('/signup', json=body)

    assert response.status_code == 422
    errors = response.json()['detail']
    assert errors and all('input' not in error for error in errors)


async def test_login(client: httpx.AsyncClient, neo: Account) -> None:
    """200: пара токенов типа bearer; access-токен даёт доступ к кабинету."""
    response = await client.post('/login', json={'login': 'neo', 'password': PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body['token_type'] == 'bearer'
    assert body['expires_in'] == 15 * 60
    claims = jwt.decode(body['access_token'], options={'verify_signature': False})
    assert (claims['sub'], claims['type']) == (neo.id, 'access')
    me = await client.get('/users/me', headers={'Authorization': f'Bearer {body["access_token"]}'})
    assert me.json()['login'] == 'neo'


async def test_login_is_case_insensitive(client: httpx.AsyncClient, neo: Account) -> None:
    """Логин при входе, как и при регистрации, сравнивается без учёта регистра."""
    await login(client, ' NeO ')


@pytest.mark.parametrize('credentials', [
    {'login': 'neo', 'password': 'wrong-password'},
    {'login': 'nobody', 'password': PASSWORD},
], ids=['wrong password', 'unknown login'])
async def test_login_invalid_credentials(client: httpx.AsyncClient, neo: Account, credentials: dict) -> None:
    """401 invalid_credentials — одинаковый для неверного пароля и неизвестного логина."""
    response = await client.post('/login', json=credentials)

    assert response.status_code == 401
    assert response.json() == {'code': 'invalid_credentials', 'detail': 'Invalid login or password'}
    assert response.headers['www-authenticate'] == 'Bearer'


@pytest.mark.parametrize('body', [{'login': 'neo'}, {'password': PASSWORD}, {'login': '', 'password': PASSWORD}])
async def test_login_invalid_body(client: httpx.AsyncClient, body: dict) -> None:
    """422, если логин или пароль не переданы."""
    response = await client.post('/login', json=body)

    assert response.status_code == 422


async def test_credentials_in_query_are_not_accepted(client: httpx.AsyncClient, neo: Account) -> None:
    """Логин и пароль принимаются только в теле: строка запроса попадает в журналы прокси."""
    response = await client.post('/login', params={'login': 'neo', 'password': PASSWORD})

    assert response.status_code == 422


async def test_login_via_get_is_not_allowed(client: httpx.AsyncClient) -> None:
    """Вход и выход меняют состояние, поэтому GET на них не отвечает — 405."""
    assert (await client.get('/login')).status_code == 405
    assert (await client.get('/logout')).status_code == 405
