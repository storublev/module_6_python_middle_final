"""Обновление токенов, выход и выход из остальных сессий."""

import asyncpg
import httpx
import jwt
import pytest

from tests.functional.conftest import Account, MakeAccount, bearer, login
from tests.functional.utils.tokens import expired, forged


async def refresh(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post('/token/refresh', json={'refresh_token': token})


async def test_refresh(client: httpx.AsyncClient, neo: Account) -> None:
    """200: новая пара, её access-токен действует, refresh-токен обновляет пару снова."""
    response = await refresh(client, neo.refresh_token)

    assert response.status_code == 200
    pair = response.json()
    assert pair['refresh_token'] != neo.refresh_token
    assert (await client.get('/users/me', headers=bearer(pair['access_token']))).status_code == 200
    assert (await refresh(client, pair['refresh_token'])).status_code == 200


async def test_refresh_token_reuse_terminates_session(client: httpx.AsyncClient, neo: Account) -> None:
    """401 token_revoked на повторное использование refresh-токена; сессия закрыта — новая пара тоже не действует."""
    pair = (await refresh(client, neo.refresh_token)).json()

    response = await refresh(client, neo.refresh_token)

    assert response.status_code == 401
    assert response.json()['code'] == 'token_revoked'
    assert (await refresh(client, pair['refresh_token'])).json()['code'] == 'token_revoked'
    assert (await client.get('/users/me', headers=bearer(pair['access_token']))).json()['code'] == 'token_revoked'


@pytest.mark.parametrize('make_token, code', [
    (lambda account: expired(account.refresh_token), 'token_expired'),
    (lambda account: forged(account.refresh_token), 'token_invalid'),
    (lambda account: account.access_token, 'token_invalid'),
    (lambda account: 'not-a-jwt', 'token_invalid'),
], ids=['expired', 'forged', 'access instead of refresh', 'malformed'])
async def test_refresh_bad_token(client: httpx.AsyncClient, neo: Account, make_token, code: str) -> None:
    """401: истёкший refresh-токен — token_expired; поддельный, повреждённый или access — token_invalid."""
    response = await refresh(client, make_token(neo))

    assert response.status_code == 401
    assert response.json()['code'] == code


async def test_refresh_after_logout(client: httpx.AsyncClient, neo: Account) -> None:
    """401 token_revoked: refresh-токен закрытой сессии не действует."""
    await client.post('/logout', headers=neo.headers)

    response = await refresh(client, neo.refresh_token)

    assert response.status_code == 401
    assert response.json()['code'] == 'token_revoked'


@pytest.mark.parametrize('body', [{}, {'refresh_token': ''}])
async def test_refresh_invalid_body(client: httpx.AsyncClient, body: dict) -> None:
    """422, если refresh-токен не передан."""
    assert (await client.post('/token/refresh', json=body)).status_code == 422


async def test_logout(client: httpx.AsyncClient, neo: Account) -> None:
    """204: после выхода не действуют ни access-, ни refresh-токен сессии."""
    response = await client.post('/logout', headers=neo.headers)

    assert response.status_code == 204
    assert (await client.get('/users/me', headers=neo.headers)).json()['code'] == 'token_revoked'
    assert (await refresh(client, neo.refresh_token)).json()['code'] == 'token_revoked'


async def test_logout_keeps_other_sessions(client: httpx.AsyncClient, neo: Account) -> None:
    """Выход закрывает только текущую сессию: вход с другого устройства продолжает работать."""
    other = await login(client, 'neo')

    await client.post('/logout', headers=neo.headers)

    assert (await client.get('/users/me', headers=bearer(other['access_token']))).status_code == 200


async def test_logout_others(client: httpx.AsyncClient, neo: Account, make_account: MakeAccount) -> None:
    """204: остальные сессии пользователя закрыты, текущая и сессии других пользователей работают."""
    other = await login(client, 'neo')
    trinity = await make_account('trinity')

    response = await client.post('/logout/others', headers=neo.headers)

    assert response.status_code == 204
    assert (await client.get('/users/me', headers=bearer(other['access_token']))).json()['code'] == 'token_revoked'
    assert (await refresh(client, other['refresh_token'])).json()['code'] == 'token_revoked'
    assert (await client.get('/users/me', headers=neo.headers)).status_code == 200
    assert (await refresh(client, neo.refresh_token)).status_code == 200
    assert (await client.get('/users/me', headers=trinity.headers)).status_code == 200


async def test_sessions_of_changed_credentials_are_revoked(
    client: httpx.AsyncClient, neo: Account, pg: asyncpg.Connection,
) -> None:
    """401 token_revoked для сессий с устаревшей версией учётных данных, хотя они остались в Redis.

    Так выглядит смена пароля, после которой удалить остальные сессии из Redis
    не удалось: версия меняется в одной транзакции с паролем. Новый вход работает.
    """
    other = await login(client, 'neo')
    await pg.execute('UPDATE auth.users SET credentials_version = credentials_version + 1 WHERE id = $1', neo.id)

    for access_token in (neo.access_token, other['access_token']):
        response = await client.get('/users/me', headers=bearer(access_token))
        assert response.status_code == 401
        assert response.json()['code'] == 'token_revoked'
    assert (await refresh(client, neo.refresh_token)).json()['code'] == 'token_revoked'
    fresh = await login(client, 'neo')
    assert (await client.get('/users/me', headers=bearer(fresh['access_token']))).status_code == 200


async def test_access_token_is_not_stored(neo: Account, redis_client) -> None:
    """В Redis лежит сессия с jti refresh-токена, но не access-токен и не его jti."""
    access_jti = jwt.decode(neo.access_token, options={'verify_signature': False})['jti']
    stored = []
    for key in await redis_client.keys('*'):
        stored.append(key)
        if await redis_client.type(key) == b'hash':
            stored.extend((await redis_client.hgetall(key)).values())
        elif await redis_client.type(key) == b'set':
            stored.extend(await redis_client.smembers(key))

    assert stored
    assert not any(neo.access_token.encode() in value or access_jti.encode() in value for value in stored)
