"""Назначение и отзыв ролей, проверка прав."""

import asyncio
import uuid

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis

from tests.functional.conftest import Account, MakeAccount, bearer
from tests.functional.utils.tokens import expired, forged

SUBSCRIPTION = 'films.subscription'


async def check(
    client: httpx.AsyncClient, account: Account | None, permission: str = SUBSCRIPTION, fresh: bool = False,
) -> bool:
    headers = account.headers if account else {}
    params = {'permission': permission, 'fresh': str(fresh).lower()}
    response = await client.get('/access/check', params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()['allowed']


async def revoke_without_cache_reset(pg: asyncpg.Connection, user_id: str, invalidation: bool) -> None:
    """Отзыв роли прямо в базе: так выглядит отзыв, после которого сбросить кеш в Redis не удалось.

    invalidation=True — вместе с отзывом записано задание на сброс кеша, как это
    делает сервис; False — задания нет, кеш так и останется устаревшим.
    """
    async with pg.transaction():
        await pg.execute('DELETE FROM auth.user_roles WHERE user_id = $1', user_id)
        if invalidation:
            await pg.execute('INSERT INTO auth.access_invalidations (user_id) VALUES ($1)', user_id)


async def test_user_roles(client: httpx.AsyncClient, admin: Account, neo: Account, subscribers_id: str) -> None:
    """200: роли пользователя; без ролей — пустой список."""
    assert (await client.get(f'/users/{neo.id}/roles', headers=admin.headers)).json() == []
    await client.put(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)

    response = await client.get(f'/users/{neo.id}/roles', headers=admin.headers)

    assert response.status_code == 200
    assert [(role['id'], role['name']) for role in response.json()] == [(subscribers_id, 'subscribers')]


async def test_user_roles_of_missing_user(client: httpx.AsyncClient, admin: Account) -> None:
    """404 user_not_found."""
    response = await client.get(f'/users/{uuid.uuid4()}/roles', headers=admin.headers)

    assert response.status_code == 404
    assert response.json()['code'] == 'user_not_found'


async def test_assign_role_is_idempotent(client: httpx.AsyncClient, admin: Account, neo: Account,
                                         subscribers_id: str) -> None:
    """204 и при первом, и при повторном назначении; роль у пользователя одна."""
    for _ in range(2):
        response = await client.put(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)
        assert response.status_code == 204

    assert len((await client.get(f'/users/{neo.id}/roles', headers=admin.headers)).json()) == 1


@pytest.mark.parametrize('method', ['PUT', 'DELETE'])
async def test_missing_user_or_role(client: httpx.AsyncClient, admin: Account, neo: Account, subscribers_id: str,
                                    method: str) -> None:
    """404 user_not_found или role_not_found при назначении и отзыве."""
    missing_user = await client.request(method, f'/users/{uuid.uuid4()}/roles/{subscribers_id}',
                                        headers=admin.headers)
    missing_role = await client.request(method, f'/users/{neo.id}/roles/{uuid.uuid4()}', headers=admin.headers)

    assert (missing_user.status_code, missing_user.json()['code']) == (404, 'user_not_found')
    assert (missing_role.status_code, missing_role.json()['code']) == (404, 'role_not_found')


async def test_revoke_role(client: httpx.AsyncClient, admin: Account, neo: Account, subscribers_id: str) -> None:
    """204: роль отобрана; повторный отзыв — 404 role_not_assigned."""
    await client.put(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)

    response = await client.delete(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)
    again = await client.delete(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)

    assert response.status_code == 204
    assert (again.status_code, again.json()['code']) == (404, 'role_not_assigned')


async def test_ids_must_be_uuid(client: httpx.AsyncClient, admin: Account, neo: Account) -> None:
    """422, если id пользователя или роли — не UUID."""
    assert (await client.get('/users/neo/roles', headers=admin.headers)).status_code == 422
    assert (await client.put(f'/users/{neo.id}/roles/subscribers', headers=admin.headers)).status_code == 422
    assert (await client.delete(f'/users/neo/roles/{uuid.uuid4()}', headers=admin.headers)).status_code == 422


async def test_check_anonymous(client: httpx.AsyncClient) -> None:
    """200: без токена проверяется анонимный пользователь — прав у него нет."""
    response = await client.get('/access/check', params={'permission': SUBSCRIPTION})

    assert response.status_code == 200
    assert response.json() == {'user_id': None, 'permission': SUBSCRIPTION, 'allowed': False}


async def test_check_follows_role_changes(client: httpx.AsyncClient, admin: Account, neo: Account,
                                          subscribers_id: str) -> None:
    """Назначение, изменение и отзыв роли сразу меняют ответ проверки, несмотря на кеш."""
    assert not await check(client, neo)

    await client.put(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)
    assert await check(client, neo)

    await client.delete(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)
    assert not await check(client, neo)


async def test_check_follows_role_permissions(client: httpx.AsyncClient, admin: Account, neo: Account) -> None:
    """Изменение и удаление роли сразу действуют у всех, кому она назначена."""
    role = (await client.post('/roles', headers=admin.headers, json={'name': 'premium'})).json()
    await client.put(f'/users/{neo.id}/roles/{role["id"]}', headers=admin.headers)
    assert not await check(client, neo, 'films.premium')

    await client.patch(f'/roles/{role["id"]}', headers=admin.headers, json={'permissions': ['films.premium']})
    assert await check(client, neo, 'films.premium')

    await client.delete(f'/roles/{role["id"]}', headers=admin.headers)
    assert not await check(client, neo, 'films.premium')


async def test_check_superuser(client: httpx.AsyncClient, admin: Account) -> None:
    """Суперпользователю разрешено любое право."""
    response = await client.get('/access/check', params={'permission': 'anything.at_all'}, headers=admin.headers)

    assert response.json() == {'user_id': admin.id, 'permission': 'anything.at_all', 'allowed': True}


async def test_check_is_cached(client: httpx.AsyncClient, neo: Account, redis_client: Redis) -> None:
    """После первой проверки права пользователя лежат в Redis."""
    await check(client, neo)

    assert await redis_client.exists(f'auth:access:{neo.id}')


async def test_role_changes_leave_no_pending_invalidations(
    client: httpx.AsyncClient, admin: Account, neo: Account, subscribers_id: str, pg: asyncpg.Connection,
) -> None:
    """Задание на сброс кеша выполняется сразу после изменения роли и удаляется из базы."""
    await client.put(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)
    await client.delete(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)

    assert await pg.fetchval('SELECT count(*) FROM auth.access_invalidations') == 0


async def test_pending_invalidation_is_retried(
    client: httpx.AsyncClient, admin: Account, neo: Account, subscribers_id: str, pg: asyncpg.Connection,
) -> None:
    """Задание, оставшееся после сбоя Redis, выполняет фоновый повтор; fresh-проверка верна сразу."""
    await client.put(f'/users/{neo.id}/roles/{subscribers_id}', headers=admin.headers)
    assert await check(client, neo)

    await revoke_without_cache_reset(pg, neo.id, invalidation=True)

    assert not await check(client, neo, fresh=True)
    for _ in range(50):
        if not await check(client, neo):
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail('Фоновый повтор не сбросил кеш прав за 5 секунд')
    assert await pg.fetchval('SELECT count(*) FROM auth.access_invalidations') == 0


async def test_manage_access_is_checked_without_cache(
    client: httpx.AsyncClient, admin: Account, make_account: MakeAccount, pg: asyncpg.Connection,
) -> None:
    """Отобранное право access.manage перестаёт действовать сразу, даже если кеш прав устарел."""
    managers = (await client.post('/roles', headers=admin.headers,
                                  json={'name': 'managers', 'permissions': ['access.manage']})).json()
    neo = await make_account('neo')
    await client.put(f'/users/{neo.id}/roles/{managers["id"]}', headers=admin.headers)
    assert await check(client, neo, 'access.manage')
    assert (await client.get('/roles', headers=neo.headers)).status_code == 200

    await revoke_without_cache_reset(pg, neo.id, invalidation=False)

    assert await check(client, neo, 'access.manage')
    response = await client.get('/roles', headers=neo.headers)
    assert (response.status_code, response.json()['code']) == (403, 'permission_denied')


@pytest.mark.parametrize('make_token, code', [
    (lambda account: expired(account.access_token), 'token_expired'),
    (lambda account: forged(account.access_token), 'token_invalid'),
], ids=['expired', 'forged'])
async def test_check_with_bad_token(client: httpx.AsyncClient, neo: Account, make_token, code: str) -> None:
    """401: недействительный токен — ошибка, а не анонимный пользователь."""
    response = await client.get('/access/check', params={'permission': SUBSCRIPTION},
                                headers=bearer(make_token(neo)))

    assert response.status_code == 401
    assert response.json()['code'] == code


@pytest.mark.parametrize('params', [{}, {'permission': 'films'}, {'permission': 'Films.Subscription'}])
async def test_check_invalid_permission(client: httpx.AsyncClient, params: dict) -> None:
    """422, если право не передано или не в формате ресурс.действие."""
    assert (await client.get('/access/check', params=params)).status_code == 422
