"""Закладки на живом стеке."""

from http import HTTPStatus

from tests.functional.conftest import API


def test_bookmark_is_stored(http, url, auth, film_id, user_id, database, cleanup):
    """Закладка появляется в MongoDB одной записью."""
    response = http.put(url(f'{API}/films/{film_id}/bookmark'), headers=auth)

    assert response.status_code == HTTPStatus.OK
    assert database.bookmarks.count_documents({'user_id': user_id, 'film_id': film_id}) == 1


def test_bookmark_is_idempotent(http, url, auth, film_id, user_id, database, cleanup):
    """Повторное добавление не создаёт вторую закладку — уникальный индекс держит пару."""
    first = http.put(url(f'{API}/films/{film_id}/bookmark'), headers=auth).json()
    second = http.put(url(f'{API}/films/{film_id}/bookmark'), headers=auth).json()

    assert first['created_at'] == second['created_at']
    assert database.bookmarks.count_documents({'user_id': user_id}) == 1


def test_bookmark_is_removed(http, url, auth, film_id, user_id, database, cleanup):
    """Удаление закладки очищает хранилище."""
    http.put(url(f'{API}/films/{film_id}/bookmark'), headers=auth)

    response = http.delete(url(f'{API}/films/{film_id}/bookmark'), headers=auth)

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert database.bookmarks.count_documents({'user_id': user_id}) == 0


def test_removing_missing_bookmark_is_404(http, url, auth, film_id):
    """Удаление того, чего нет, — 404 с машиночитаемым кодом."""
    response = http.delete(url(f'{API}/films/{film_id}/bookmark'), headers=auth)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'bookmark_not_found'


def test_bookmarks_are_listed(http, url, auth, film_id, cleanup):
    """Закладка видна в списке зрителя."""
    http.put(url(f'{API}/films/{film_id}/bookmark'), headers=auth)

    body = http.get(url(f'{API}/users/me/bookmarks'), headers=auth).json()

    assert body['total'] == 1
    assert body['items'][0]['film_id'] == str(film_id)


def test_bookmarks_require_token(http, url):
    """Список закладок без токена не отдаётся."""
    response = http.get(url(f'{API}/users/me/bookmarks'))

    assert response.status_code == HTTPStatus.UNAUTHORIZED
