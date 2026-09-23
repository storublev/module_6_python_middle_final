"""Закладки: добавление, идемпотентность, удаление и список."""

from http import HTTPStatus
from uuid import uuid4

from tests.unit.conftest import API, FILM_ID


def test_add_bookmark(client, auth, user_id):
    """Закладка создаётся, зритель берётся из токена."""
    response = client.put(f'{API}/films/{FILM_ID}/bookmark', headers=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json()['film_id'] == str(FILM_ID)
    assert response.json()['user_id'] == str(user_id)


def test_add_bookmark_twice_is_idempotent(client, auth, bookmarks_storage):
    """Повторное добавление не создаёт вторую закладку и не меняет время (ФТ-9)."""
    first = client.put(f'{API}/films/{FILM_ID}/bookmark', headers=auth).json()
    second = client.put(f'{API}/films/{FILM_ID}/bookmark', headers=auth).json()

    assert first['created_at'] == second['created_at']
    assert len(bookmarks_storage.items) == 1


def test_remove_bookmark(client, auth, bookmarks_storage):
    """Удаление закладки отвечает 204 и очищает хранилище."""
    client.put(f'{API}/films/{FILM_ID}/bookmark', headers=auth)

    response = client.delete(f'{API}/films/{FILM_ID}/bookmark', headers=auth)

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert bookmarks_storage.items == {}


def test_remove_missing_bookmark_returns_404(client, auth):
    """Удаление того, чего в закладках нет, — 404 с кодом."""
    response = client.delete(f'{API}/films/{FILM_ID}/bookmark', headers=auth)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'bookmark_not_found'


def test_bookmarks_are_listed_in_order_of_adding(client, auth):
    """Список закладок идёт в порядке добавления."""
    films = [uuid4() for _ in range(2)]
    for film in films:
        client.put(f'{API}/films/{film}/bookmark', headers=auth)

    items = client.get(f'{API}/users/me/bookmarks', headers=auth).json()['items']

    assert [item['film_id'] for item in items] == [str(film) for film in films]


def test_bookmarks_of_other_user_are_not_visible(client, auth, make_token):
    """Зритель видит только свои закладки."""
    client.put(f'{API}/films/{FILM_ID}/bookmark', headers=auth)
    stranger = {'Authorization': f'Bearer {make_token(uuid4())}'}

    response = client.get(f'{API}/users/me/bookmarks', headers=stranger)

    assert response.json()['total'] == 0


def test_bookmarks_require_token(client):
    """Список закладок без токена не отдаётся."""
    response = client.get(f'{API}/users/me/bookmarks')

    assert response.status_code == HTTPStatus.UNAUTHORIZED
