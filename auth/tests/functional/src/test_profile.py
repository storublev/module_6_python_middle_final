"""Личный кабинет: данные пользователя, смена логина и пароля, история входов."""

import httpx
import pytest

from tests.functional.conftest import PASSWORD, Account, MakeAccount, bearer, login


async def test_me(client: httpx.AsyncClient, neo: Account) -> None:
    """200: данные пользователя без пароля и ролей."""
    response = await client.get('/users/me', headers=neo.headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'id', 'login', 'created_at', 'is_superuser', 'roles'}
    assert (body['id'], body['login'], body['is_superuser'], body['roles']) == (neo.id, 'neo', False, [])


async def test_me_shows_roles(client: httpx.AsyncClient, neo: Account, admin: Account, subscribers_id: str) -> None:
    """В данных пользователя видны назначенные ему роли."""
    await client.put(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)

    body = (await client.get('/users/me', headers=neo.headers)).json()

    assert body['roles'] == [{'id': subscribers_id, 'name': 'subscribers'}]


async def test_change_login(client: httpx.AsyncClient, neo: Account) -> None:
    """200: логин сменён; войти можно с новым логином, со старым — нет."""
    response = await client.patch('/users/me/login', headers=neo.headers,
                                  json={'new_login': 'TheOne', 'password': PASSWORD})

    assert response.status_code == 200
    assert response.json()['login'] == 'theone'
    await login(client, 'theone')
    assert (await client.post('/login', json={'login': 'neo', 'password': PASSWORD})).status_code == 401


async def test_change_login_to_same(client: httpx.AsyncClient, neo: Account) -> None:
    """200: смена логина на тот же самый — не конфликт."""
    response = await client.patch('/users/me/login', headers=neo.headers,
                                  json={'new_login': 'NEO', 'password': PASSWORD})

    assert response.status_code == 200


async def test_change_login_wrong_password(client: httpx.AsyncClient, neo: Account) -> None:
    """403 wrong_password: смену логина подтверждает текущий пароль."""
    response = await client.patch('/users/me/login', headers=neo.headers,
                                  json={'new_login': 'theone', 'password': 'wrong-password'})

    assert response.status_code == 403
    assert response.json()['code'] == 'wrong_password'


async def test_change_login_taken(client: httpx.AsyncClient, neo: Account, make_account: MakeAccount) -> None:
    """409 login_taken: логин другого пользователя занять нельзя."""
    await make_account('trinity')

    response = await client.patch('/users/me/login', headers=neo.headers,
                                  json={'new_login': 'Trinity', 'password': PASSWORD})

    assert response.status_code == 409
    assert response.json()['code'] == 'login_taken'


@pytest.mark.parametrize('body', [{'new_login': 'ab', 'password': PASSWORD}, {'new_login': 'theone'}])
async def test_change_login_invalid(client: httpx.AsyncClient, neo: Account, body: dict) -> None:
    """422 на неверный новый логин или без текущего пароля."""
    assert (await client.patch('/users/me/login', headers=neo.headers, json=body)).status_code == 422


async def test_change_password(client: httpx.AsyncClient, neo: Account) -> None:
    """204: новый пароль действует, старый — нет; остальные сессии закрыты, текущая работает."""
    other = await login(client, 'neo')

    response = await client.put('/users/me/password', headers=neo.headers,
                                json={'password': PASSWORD, 'new_password': 'new-password'})

    assert response.status_code == 204
    await login(client, 'neo', 'new-password')
    assert (await client.post('/login', json={'login': 'neo', 'password': PASSWORD})).status_code == 401
    assert (await client.get('/users/me', headers=bearer(other['access_token']))).json()['code'] == 'token_revoked'
    assert (await client.get('/users/me', headers=neo.headers)).status_code == 200


async def test_change_password_wrong_password(client: httpx.AsyncClient, neo: Account) -> None:
    """403 wrong_password: без верного текущего пароля пароль не меняется."""
    response = await client.put('/users/me/password', headers=neo.headers,
                                json={'password': 'wrong-password', 'new_password': 'new-password'})

    assert response.status_code == 403
    assert response.json()['code'] == 'wrong_password'
    await login(client, 'neo')


@pytest.mark.parametrize('body', [{'password': PASSWORD, 'new_password': 'short'}, {'password': PASSWORD}])
async def test_change_password_invalid(client: httpx.AsyncClient, neo: Account, body: dict) -> None:
    """422 на слишком короткий новый пароль или без него."""
    assert (await client.put('/users/me/password', headers=neo.headers, json=body)).status_code == 422


async def test_login_history(client: httpx.AsyncClient, neo: Account) -> None:
    """200: входы от новых к старым, с устройством и IP."""
    await login(client, 'neo', headers={'User-Agent': 'phone'})
    await login(client, 'neo', headers={'User-Agent': 'tv'})

    response = await client.get('/users/me/login-history', headers=neo.headers)

    assert response.status_code == 200
    records = response.json()
    assert [record['user_agent'] for record in records][:2] == ['tv', 'phone']
    assert len(records) == 3
    assert all(record['ip'] and record['created_at'] for record in records)


async def test_login_history_pages(client: httpx.AsyncClient, neo: Account) -> None:
    """Постраничный вывод: page_size записей, page_number с 1."""
    for number in range(4):
        await login(client, 'neo', headers={'User-Agent': f'device-{number}'})

    page = await client.get('/users/me/login-history', headers=neo.headers,
                            params={'page_number': 2, 'page_size': 2})

    assert [record['user_agent'] for record in page.json()] == ['device-1', 'device-0']


async def test_login_history_is_private(client: httpx.AsyncClient, neo: Account, make_account: MakeAccount) -> None:
    """В истории только свои входы."""
    trinity = await make_account('trinity')
    await login(client, 'trinity', headers={'User-Agent': 'trinity-phone'})

    records = (await client.get('/users/me/login-history', headers=neo.headers)).json()
    trinity_records = (await client.get('/users/me/login-history', headers=trinity.headers)).json()

    assert len(records) == 1
    assert 'trinity-phone' not in [record['user_agent'] for record in records]
    assert len(trinity_records) == 2


@pytest.mark.parametrize('params', [{'page_size': 0}, {'page_size': 101}, {'page_number': 0}, {'page_number': 'x'}])
async def test_login_history_invalid_page(client: httpx.AsyncClient, neo: Account, params: dict) -> None:
    """422 на неверные параметры страницы."""
    response = await client.get('/users/me/login-history', headers=neo.headers, params=params)

    assert response.status_code == 422
