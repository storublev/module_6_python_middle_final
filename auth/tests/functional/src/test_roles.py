"""CRUD ролей."""

import uuid

import httpx
import pytest

from tests.functional.conftest import Account, MakeAccount

INVALID_ROLES = {
    'upper case name': {'name': 'Editors'},
    'short name': {'name': 'e'},
    'name with space': {'name': 'film editors'},
    'permission without action': {'name': 'editors', 'permissions': ['films']},
    'permission in upper case': {'name': 'editors', 'permissions': ['Films.Edit']},
    'empty permission part': {'name': 'editors', 'permissions': ['films..edit']},
    'no name': {'permissions': ['films.edit']},
}


async def create_role(client: httpx.AsyncClient, admin: Account, name: str = 'editors', **fields) -> dict:
    response = await client.post('/roles', headers=admin.headers, json={'name': name, **fields})
    assert response.status_code == 201, response.text
    return response.json()


async def test_list_roles(client: httpx.AsyncClient, admin: Account) -> None:
    """200: роли по имени, среди них subscribers из миграции с правом films.subscription."""
    await create_role(client, admin, 'editors')

    response = await client.get('/roles', headers=admin.headers)

    assert response.status_code == 200
    roles = response.json()
    assert [role['name'] for role in roles] == ['editors', 'subscribers']
    assert roles[1]['permissions'] == ['films.subscription']


async def test_create_role(client: httpx.AsyncClient, admin: Account) -> None:
    """201: роль создана; повторы в списке прав убраны."""
    response = await client.post('/roles', headers=admin.headers, json={
        'name': 'editors', 'description': 'Редакторы', 'permissions': ['films.edit', 'films.edit', 'genres.edit'],
    })

    assert response.status_code == 201
    role = response.json()
    assert (role['name'], role['description'], role['permissions']) == (
        'editors', 'Редакторы', ['films.edit', 'genres.edit'],
    )


async def test_create_role_name_taken(client: httpx.AsyncClient, admin: Account) -> None:
    """409 role_name_taken: имя роли уникально."""
    response = await client.post('/roles', headers=admin.headers, json={'name': 'subscribers'})

    assert response.status_code == 409
    assert response.json()['code'] == 'role_name_taken'


@pytest.mark.parametrize('body', INVALID_ROLES.values(), ids=INVALID_ROLES.keys())
async def test_create_role_invalid(client: httpx.AsyncClient, admin: Account, body: dict) -> None:
    """422 на неверное имя роли или право."""
    assert (await client.post('/roles', headers=admin.headers, json=body)).status_code == 422


async def test_get_role(client: httpx.AsyncClient, admin: Account) -> None:
    """200: роль по id."""
    role = await create_role(client, admin, permissions=['films.edit'])

    response = await client.get(f'/roles/{role["id"]}', headers=admin.headers)

    assert response.status_code == 200
    assert response.json() == role


async def test_get_missing_role(client: httpx.AsyncClient, admin: Account) -> None:
    """404 role_not_found."""
    response = await client.get(f'/roles/{uuid.uuid4()}', headers=admin.headers)

    assert response.status_code == 404
    assert response.json()['code'] == 'role_not_found'


async def test_role_id_must_be_uuid(client: httpx.AsyncClient, admin: Account) -> None:
    """422, если id роли — не UUID."""
    assert (await client.get('/roles/editors', headers=admin.headers)).status_code == 422


async def test_update_role(client: httpx.AsyncClient, admin: Account) -> None:
    """200: меняются только переданные поля."""
    role = await create_role(client, admin, description='Редакторы', permissions=['films.edit'])

    response = await client.patch(f'/roles/{role["id"]}', headers=admin.headers,
                                  json={'name': 'film-editors', 'permissions': ['films.edit', 'films.publish']})

    assert response.status_code == 200
    updated = response.json()
    assert (updated['name'], updated['description'], updated['permissions']) == (
        'film-editors', 'Редакторы', ['films.edit', 'films.publish'],
    )


async def test_update_role_clears_description(client: httpx.AsyncClient, admin: Account) -> None:
    """Описание можно стереть, передав null."""
    role = await create_role(client, admin, description='Редакторы')

    response = await client.patch(f'/roles/{role["id"]}', headers=admin.headers, json={'description': None})

    assert response.json()['description'] is None


async def test_update_missing_role(client: httpx.AsyncClient, admin: Account) -> None:
    """404 role_not_found."""
    response = await client.patch(f'/roles/{uuid.uuid4()}', headers=admin.headers, json={'description': 'x'})

    assert response.status_code == 404
    assert response.json()['code'] == 'role_not_found'


async def test_rename_role_to_taken_name(client: httpx.AsyncClient, admin: Account) -> None:
    """409 role_name_taken: нельзя переименовать роль в имя другой роли."""
    role = await create_role(client, admin)

    response = await client.patch(f'/roles/{role["id"]}', headers=admin.headers, json={'name': 'subscribers'})

    assert response.status_code == 409
    assert response.json()['code'] == 'role_name_taken'


@pytest.mark.parametrize('body', [{'name': None}, {'permissions': None}, {'permissions': ['films']}])
async def test_update_role_invalid(client: httpx.AsyncClient, admin: Account, body: dict) -> None:
    """422: имя и права нельзя стереть, право должно быть в формате ресурс.действие."""
    role = await create_role(client, admin)

    assert (await client.patch(f'/roles/{role["id"]}', headers=admin.headers, json=body)).status_code == 422


async def test_delete_role(client: httpx.AsyncClient, admin: Account, neo: Account) -> None:
    """204: роль удалена и отобрана у пользователей."""
    role = await create_role(client, admin)
    await client.put(f'/users/{neo.id}/roles/{role["id"]}', headers=admin.headers)

    response = await client.delete(f'/roles/{role["id"]}', headers=admin.headers)

    assert response.status_code == 204
    assert (await client.get(f'/roles/{role["id"]}', headers=admin.headers)).status_code == 404
    assert (await client.get(f'/users/{neo.id}/roles', headers=admin.headers)).json() == []


async def test_delete_missing_role(client: httpx.AsyncClient, admin: Account) -> None:
    """404 role_not_found."""
    response = await client.delete(f'/roles/{uuid.uuid4()}', headers=admin.headers)

    assert response.status_code == 404
    assert response.json()['code'] == 'role_not_found'


async def test_access_manager_is_not_superuser(client: httpx.AsyncClient, admin: Account,
                                               make_account: MakeAccount) -> None:
    """Управлять ролями может не только суперпользователь, но и обладатель права access.manage."""
    managers = await create_role(client, admin, 'access-managers', permissions=['access.manage'])
    manager = await make_account('manager')
    await client.put(f'/users/{manager.id}/roles/{managers["id"]}', headers=admin.headers)

    assert (await client.get('/roles', headers=manager.headers)).status_code == 200
