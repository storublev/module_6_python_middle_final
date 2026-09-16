"""Вход через соцсети и связанные аккаунты в личном кабинете.

Настоящего поставщика в окружении тестов нет, поэтому здесь проверяется всё,
что видно снаружи до ухода к нему и после возврата: список поставщиков, ссылка
авторизации со state, отказы на неверный возврат, а также личный кабинет —
список связанных аккаунтов и открепление. Сам обмен кода на данные
пользователя проверяют модульные тесты с подменённым транспортом.
"""

from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

import pytest

from tests.functional.conftest import Account

PROVIDER = 'yandex'
UNKNOWN_PROVIDER = 'facebook'


async def authorize_url(client) -> str:
    """Ссылка на поставщика из ответа на начало входа."""
    response = await client.get(f'/oauth/{PROVIDER}/login', follow_redirects=False)
    assert response.status_code == HTTPStatus.TEMPORARY_REDIRECT, response.text
    return response.headers['location']


# Список поставщиков

async def test_configured_provider_is_listed(client):
    """Поставщик с ключами приложения доступен для входа."""
    response = await client.get('/oauth/providers')

    assert response.status_code == HTTPStatus.OK
    assert [provider['name'] for provider in response.json()] == [PROVIDER]


async def test_provider_has_a_title(client):
    """У поставщика есть название для человека — его показывают на кнопке входа."""
    response = await client.get('/oauth/providers')

    assert response.json()[0]['title']


# Начало входа

async def test_login_redirects_to_provider(client):
    """Вход начинается переходом на страницу поставщика."""
    assert urlparse(await authorize_url(client)).netloc == 'oauth.yandex.ru'


@pytest.mark.parametrize('param', ['client_id', 'redirect_uri', 'state'])
async def test_authorize_url_carries_parameters(client, param):
    """В ссылке есть всё, что нужно поставщику, и наш state."""
    assert parse_qs(urlparse(await authorize_url(client)).query).get(param)


async def test_authorization_code_flow_is_used(client):
    """Запрашивается код, а не токен: токен в адресной строке перехватывается."""
    query = parse_qs(urlparse(await authorize_url(client)).query)

    assert query['response_type'] == ['code']


async def test_client_secret_is_not_exposed(client):
    """Секрет приложения в браузер не уходит."""
    assert 'functional-tests-client-secret' not in await authorize_url(client)


async def test_each_login_gets_its_own_state(client):
    """У каждого начатого входа свой state: чужой возврат к нему не подойдёт."""
    first = parse_qs(urlparse(await authorize_url(client)).query)['state']
    second = parse_qs(urlparse(await authorize_url(client)).query)['state']

    assert first != second


async def test_unknown_provider_is_not_found(client):
    """Через ненастроенного поставщика войти нельзя."""
    response = await client.get(f'/oauth/{UNKNOWN_PROVIDER}/login', follow_redirects=False)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'provider_not_found'


# Возврат от поставщика

async def test_callback_of_unknown_provider_is_not_found(client):
    """Возврат от ненастроенного поставщика тоже отвергается."""
    response = await client.get(f'/oauth/{UNKNOWN_PROVIDER}/callback', params={'code': 'x', 'state': 'y'})

    assert response.status_code == HTTPStatus.NOT_FOUND


async def test_callback_with_foreign_state_is_rejected(client):
    """Возврат с чужим state отвергается: так к нам не привяжут чужой аккаунт."""
    response = await client.get(f'/oauth/{PROVIDER}/callback', params={'code': 'x', 'state': 'not-ours'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'oauth_state_invalid'


async def test_callback_without_state_is_rejected(client):
    """Возврат без state тоже отвергается."""
    response = await client.get(f'/oauth/{PROVIDER}/callback', params={'code': 'x'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED


async def test_provider_error_is_reported(client):
    """Отказ пользователя у поставщика — понятная ошибка, а не пятисотка."""
    state = parse_qs(urlparse(await authorize_url(client)).query)['state'][0]

    response = await client.get(f'/oauth/{PROVIDER}/callback', params={'error': 'access_denied', 'state': state})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'oauth_rejected'


async def test_abandoned_login_state_stops_working(client):
    """Брошенный вход гасится: его state не сработает позже."""
    state = parse_qs(urlparse(await authorize_url(client)).query)['state'][0]
    await client.get(f'/oauth/{PROVIDER}/callback', params={'error': 'access_denied', 'state': state})

    response = await client.get(f'/oauth/{PROVIDER}/callback', params={'code': 'x', 'state': state})

    assert response.json()['code'] == 'oauth_state_invalid'


# Личный кабинет

async def test_new_account_has_no_linked_accounts(client, neo: Account):
    """У обычной учётной записи связанных аккаунтов нет."""
    response = await client.get('/users/me/social-accounts', headers=neo.headers)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == []


async def test_linked_accounts_require_a_token(client):
    """Список связанных аккаунтов — личные данные: без токена его не получить."""
    response = await client.get('/users/me/social-accounts')

    assert response.status_code == HTTPStatus.UNAUTHORIZED


async def test_unlinking_missing_account_is_not_found(client, neo: Account):
    """Открепление непривязанного аккаунта — 404, а не молчаливый успех."""
    response = await client.delete(f'/users/me/social-accounts/{PROVIDER}', headers=neo.headers)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'social_account_not_linked'


async def test_unlinking_requires_a_token(client):
    """Открепить чужой аккаунт без токена нельзя."""
    response = await client.delete(f'/users/me/social-accounts/{PROVIDER}')

    assert response.status_code == HTTPStatus.UNAUTHORIZED


async def test_linked_account_is_visible(client, neo: Account, pg):
    """Привязанный аккаунт виден в личном кабинете вместе с именем и почтой."""
    await link_account(pg, neo.id, social_id='42', display_name='Neo', email='neo@example.com')

    response = await client.get('/users/me/social-accounts', headers=neo.headers)

    account = response.json()[0]
    assert (account['provider'], account['social_id'], account['email']) == (PROVIDER, '42', 'neo@example.com')


async def test_linked_account_can_be_unlinked(client, neo: Account, pg):
    """Пока есть пароль, аккаунт соцсети откреплятся: способ войти остаётся."""
    await link_account(pg, neo.id, social_id='42')

    response = await client.delete(f'/users/me/social-accounts/{PROVIDER}', headers=neo.headers)

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert (await client.get('/users/me/social-accounts', headers=neo.headers)).json() == []


async def test_only_own_accounts_are_listed(client, make_account, pg):
    """В личном кабинете видны только свои связанные аккаунты."""
    owner = await make_account('neo')
    other = await make_account('trinity')
    await link_account(pg, owner.id, social_id='42')

    response = await client.get('/users/me/social-accounts', headers=other.headers)

    assert response.json() == []


# Учётная запись без пароля

async def test_user_without_password_cannot_unlink_last_account(client, neo: Account, pg):
    """Единственный способ войти открепить нельзя: пользователь потерял бы доступ."""
    await link_account(pg, neo.id, social_id='42')
    await pg.execute('UPDATE auth.users SET password_hash = NULL WHERE id = $1', neo.id)

    response = await client.delete(f'/users/me/social-accounts/{PROVIDER}', headers=neo.headers)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()['code'] == 'last_login_method'


async def test_user_without_password_sets_the_first_one(client, neo: Account, pg):
    """Пользователь из соцсети задаёт первый пароль без подтверждения текущим: его нет."""
    await pg.execute('UPDATE auth.users SET password_hash = NULL WHERE id = $1', neo.id)

    response = await client.put(
        '/users/me/password', json={'new_password': 'brandnewpassword'}, headers=neo.headers,
    )

    assert response.status_code == HTTPStatus.NO_CONTENT


async def test_user_with_password_must_confirm_it(client, neo: Account):
    """У кого пароль есть, тот подтверждает смену текущим: одного украденного токена мало."""
    response = await client.put(
        '/users/me/password', json={'new_password': 'brandnewpassword'}, headers=neo.headers,
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()['code'] == 'password_already_set'


async def test_password_login_fails_without_a_password(client, neo: Account, pg):
    """В учётную запись без пароля по паролю не войти."""
    await pg.execute('UPDATE auth.users SET password_hash = NULL WHERE id = $1', neo.id)

    response = await client.post('/login', json={'login': neo.login, 'password': neo.password})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'invalid_credentials'


async def test_account_becomes_available_after_setting_a_password(client, neo: Account, pg):
    """Задав пароль, пользователь из соцсети может входить и по нему."""
    await pg.execute('UPDATE auth.users SET password_hash = NULL WHERE id = $1', neo.id)
    await client.put('/users/me/password', json={'new_password': 'brandnewpassword'}, headers=neo.headers)

    response = await client.post('/login', json={'login': neo.login, 'password': 'brandnewpassword'})

    assert response.status_code == HTTPStatus.OK


async def link_account(pg, user_id: str, social_id: str, display_name=None, email=None) -> None:
    """Привязывает аккаунт соцсети в обход поставщика: его в окружении тестов нет."""
    await pg.execute(
        'INSERT INTO auth.social_accounts (id, user_id, provider, social_id, display_name, email) '
        'VALUES (gen_random_uuid(), $1, $2, $3, $4, $5)',
        user_id, PROVIDER, social_id, display_name, email,
    )
