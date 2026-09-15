"""Ответы 401 на каждом эндпоинте, которому нужен access-токен.

Клиент по коду ошибки понимает, что делать: на token_expired — обновить
пару токенов, на token_invalid и token_revoked — войти заново, на
not_authenticated — передать токен.
"""

import uuid
from collections.abc import Callable

import httpx
import pytest

from tests.functional.conftest import Account, bearer
from tests.functional.utils.tokens import expired, forged

ROLE = str(uuid.uuid4())
USER = str(uuid.uuid4())

PROTECTED = [
    ('POST', '/logout', None),
    ('POST', '/logout/others', None),
    ('GET', '/users/me', None),
    ('PATCH', '/users/me/login', {'new_login': 'theone', 'password': 'followtherabbit'}),
    ('PUT', '/users/me/password', {'password': 'followtherabbit', 'new_password': 'new-password'}),
    ('GET', '/users/me/login-history', None),
    ('GET', '/roles', None),
    ('POST', '/roles', {'name': 'editors'}),
    ('GET', f'/roles/{ROLE}', None),
    ('PATCH', f'/roles/{ROLE}', {'description': 'x'}),
    ('DELETE', f'/roles/{ROLE}', None),
    ('GET', f'/users/{USER}/roles', None),
    ('PUT', f'/users/{USER}/roles/{ROLE}', None),
    ('DELETE', f'/users/{USER}/roles/{ROLE}', None),
]
ENDPOINT_IDS = [f'{method} {path}' for method, path, _ in PROTECTED]

BAD_TOKENS: dict[str, tuple[Callable[[Account], str], str]] = {
    'expired': (lambda account: expired(account.access_token), 'token_expired'),
    'forged': (lambda account: forged(account.access_token), 'token_invalid'),
    'refresh instead of access': (lambda account: account.refresh_token, 'token_invalid'),
    'malformed': (lambda account: 'not-a-jwt', 'token_invalid'),
}


@pytest.mark.parametrize('method, path, body', PROTECTED, ids=ENDPOINT_IDS)
async def test_without_token(client: httpx.AsyncClient, method: str, path: str, body: dict | None) -> None:
    """Без токена — 401 not_authenticated и заголовок WWW-Authenticate: Bearer."""
    response = await client.request(method, path, json=body)

    assert response.status_code == 401
    assert response.json()['code'] == 'not_authenticated'
    assert response.headers['www-authenticate'] == 'Bearer'


@pytest.mark.parametrize('method, path, body', PROTECTED, ids=ENDPOINT_IDS)
async def test_non_bearer_scheme(client: httpx.AsyncClient, neo: Account, method: str, path: str,
                                 body: dict | None) -> None:
    """Токен не в схеме Bearer не принимается — 401 not_authenticated."""
    response = await client.request(method, path, json=body, headers={'Authorization': f'Basic {neo.access_token}'})

    assert response.status_code == 401
    assert response.json()['code'] == 'not_authenticated'


@pytest.mark.parametrize('kind', BAD_TOKENS)
@pytest.mark.parametrize('method, path, body', PROTECTED, ids=ENDPOINT_IDS)
async def test_bad_token(client: httpx.AsyncClient, neo: Account, method: str, path: str, body: dict | None,
                         kind: str) -> None:
    """Истёкший токен — token_expired, поддельный, повреждённый или refresh вместо access — token_invalid."""
    make_token, code = BAD_TOKENS[kind]

    response = await client.request(method, path, json=body, headers=bearer(make_token(neo)))

    assert response.status_code == 401
    assert response.json()['code'] == code
    assert response.headers['www-authenticate'].startswith('Bearer error="invalid_token"')


@pytest.mark.parametrize('method, path, body', PROTECTED, ids=ENDPOINT_IDS)
async def test_token_of_closed_session(client: httpx.AsyncClient, neo: Account, method: str, path: str,
                                       body: dict | None) -> None:
    """Токен сессии, из которой вышли, не действует, хотя его срок ещё не истёк — token_revoked."""
    assert (await client.post('/logout', headers=neo.headers)).status_code == 204

    response = await client.request(method, path, json=body, headers=neo.headers)

    assert response.status_code == 401
    assert response.json()['code'] == 'token_revoked'


ADMIN_ONLY = [endpoint for endpoint in PROTECTED if endpoint[1].startswith(('/roles', f'/users/{USER}'))]


@pytest.mark.parametrize('method, path, body', ADMIN_ONLY, ids=[f'{method} {path}' for method, path, _ in ADMIN_ONLY])
async def test_access_management_requires_permission(client: httpx.AsyncClient, neo: Account, method: str,
                                                     path: str, body: dict | None) -> None:
    """Управлять ролями без права access.manage нельзя — 403 permission_denied."""
    response = await client.request(method, path, json=body, headers=neo.headers)

    assert response.status_code == 403
    assert response.json() == {'code': 'permission_denied', 'detail': 'Permission access.manage is required'}
