"""Оценки фильмов на живом стеке: сервис, MongoDB и счётчик агрегата."""

from http import HTTPStatus
from uuid import uuid4

from tests.functional.conftest import API


def test_rating_is_stored_in_mongo(http, url, auth, film_id, user_id, database, cleanup):
    """Поставленная оценка появляется в коллекции likes ровно одной записью."""
    response = http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 9}, headers=auth)

    assert response.status_code == HTTPStatus.OK
    documents = list(database.likes.find({'film_id': film_id, 'user_id': user_id}))
    assert len(documents) == 1
    assert documents[0]['rating'] == 9


def test_rating_moves_film_counter(http, url, auth, film_id, database, cleanup):
    """Счётчик фильма двигается вместе с оценкой — на нём и стоит карточка."""
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 10}, headers=auth)

    counter = database.film_ratings.find_one({'film_id': film_id})
    assert counter['likes'] == 1
    assert counter['dislikes'] == 0
    assert counter['votes'] == 1
    assert counter['sum_rating'] == 10


def test_changed_rating_does_not_double_counter(http, url, auth, film_id, database, cleanup):
    """Замена оценки не добавляет второй голос: счётчик считает зрителей, а не нажатия."""
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 10}, headers=auth)
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 2}, headers=auth)

    counter = database.film_ratings.find_one({'film_id': film_id})
    assert counter['votes'] == 1
    assert counter['likes'] == 0
    assert counter['dislikes'] == 1
    assert counter['sum_rating'] == 2


def test_removed_rating_returns_counter_back(http, url, auth, film_id, database, cleanup):
    """Снятая оценка уходит из счётчика, а не остаётся в нём навсегда."""
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 8}, headers=auth)
    response = http.delete(url(f'{API}/films/{film_id}/rating'), headers=auth)

    assert response.status_code == HTTPStatus.NO_CONTENT
    counter = database.film_ratings.find_one({'film_id': film_id})
    assert counter['votes'] == 0
    assert counter['likes'] == 0


def test_aggregate_is_visible_immediately(http, url, auth, film_id, cleanup):
    """Оценка видна в агрегате сразу после ответа сервиса (НФТ-6)."""
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 7}, headers=auth)

    response = http.get(url(f'{API}/films/{film_id}/rating'))

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        'film_id': str(film_id),
        'likes': 1,
        'dislikes': 0,
        'average_rating': 7.0,
    }


def test_aggregate_counts_several_viewers(http, url, make_token, film_id, database, cleanup):
    """Агрегат складывает оценки разных зрителей и считает среднее."""
    for rating in (10, 9, 1):
        headers = {'Authorization': f'Bearer {make_token(uuid4())}'}
        http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': rating}, headers=headers)

    body = http.get(url(f'{API}/films/{film_id}/rating')).json()

    assert body['likes'] == 2
    assert body['dislikes'] == 1
    assert body['average_rating'] == 6.67
    database.likes.delete_many({'film_id': film_id})


def test_unknown_film_has_empty_rating(http, url):
    """Фильм без оценок отвечает нулями, а не ошибкой."""
    response = http.get(url(f'{API}/films/{uuid4()}/rating'))

    assert response.status_code == HTTPStatus.OK
    assert response.json()['likes'] == 0
    assert response.json()['average_rating'] is None


def test_rating_requires_token(http, url, film_id):
    """Без токена оценку поставить нельзя."""
    response = http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 5})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'not_authenticated'


def test_liked_films_are_listed(http, url, auth, film_id, user_id, database, cleanup):
    """Понравившийся фильм попадает в список зрителя, нелюбимый — нет."""
    disliked = uuid4()
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 9}, headers=auth)
    http.put(url(f'{API}/films/{disliked}/rating'), json={'rating': 1}, headers=auth)

    body = http.get(url(f'{API}/users/me/likes'), headers=auth).json()

    assert body['total'] == 1
    assert body['items'][0]['film_id'] == str(film_id)
    database.likes.delete_many({'film_id': disliked})
    database.film_ratings.delete_many({'film_id': disliked})


def test_my_rating_returns_own_value(http, url, auth, film_id, cleanup):
    """Своя оценка читается отдельным эндпоинтом."""
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 4}, headers=auth)

    response = http.get(url(f'{API}/films/{film_id}/rating/me'), headers=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json()['rating'] == 4


def test_my_rating_is_404_for_other_viewer(http, url, auth, make_token, film_id, cleanup):
    """Чужая оценка через «мою» не читается."""
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 4}, headers=auth)
    stranger = {'Authorization': f'Bearer {make_token(uuid4())}'}

    response = http.get(url(f'{API}/films/{film_id}/rating/me'), headers=stranger)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'rating_not_found'
