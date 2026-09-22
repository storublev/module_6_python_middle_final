"""Проверка access-токена: что принимается, а что нет."""

from datetime import timedelta
from http import HTTPStatus
from uuid import uuid4

import pytest

from tests.unit.conftest import API, FILM_ID

RATING = {'rating': 7}


def test_request_without_token_is_rejected(client):
    """Без заголовка Authorization — 401 и схема аутентификации в ответе."""
    response = client.put(f'{API}/films/{FILM_ID}/rating', json=RATING)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'not_authenticated'
    assert response.headers['WWW-Authenticate'].startswith('Bearer')


@pytest.mark.parametrize('header', ['token', 'Basic abc', 'Bearer', 'Bearer   '])
def test_malformed_authorization_header_is_rejected(client, header):
    """Заголовок не формата «Bearer <токен>» не принимается."""
    response = client.put(f'{API}/films/{FILM_ID}/rating', json=RATING, headers={'Authorization': header})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'not_authenticated'


def test_expired_token_is_rejected(client, make_token):
    """Истёкший токен отвечает token_expired, а не token_invalid."""
    token = make_token(uuid4(), ttl=timedelta(minutes=-1))

    response = client.put(f'{API}/films/{FILM_ID}/rating', json=RATING,
                          headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_expired'
    assert 'invalid_token' in response.headers['WWW-Authenticate']


def test_token_signed_with_other_secret_is_rejected(client, make_token):
    """Чужая подпись не проходит: секрет общий только с сервисом авторизации."""
    token = make_token(uuid4(), secret='another-secret-key-of-at-least-32-bytes')

    response = client.put(f'{API}/films/{FILM_ID}/rating', json=RATING,
                          headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_invalid'


def test_refresh_token_is_rejected(client, make_token):
    """Refresh-токеном пользоваться нельзя: он живёт две недели."""
    token = make_token(uuid4(), token_type='refresh')

    response = client.put(f'{API}/films/{FILM_ID}/rating', json=RATING,
                          headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_invalid'


@pytest.mark.parametrize('claim', ['exp', 'sub', 'sid', 'jti', 'type', 'iat'])
def test_token_without_required_claim_is_rejected(client, make_token, claim):
    """Токен без обязательного поля не принимается — иначе он был бы вечным."""
    token = make_token(uuid4(), drop=claim)

    response = client.put(f'{API}/films/{FILM_ID}/rating', json=RATING,
                          headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_invalid'


def test_token_with_broken_sub_is_rejected(client, make_token):
    """Если sub не UUID, токен не принимается: это не наш формат."""
    token = make_token(sub='not-a-uuid')

    response = client.put(f'{API}/films/{FILM_ID}/rating', json=RATING,
                          headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_invalid'
