"""Оценки фильмов: постановка, снятие, агрегат и список понравившегося."""

from http import HTTPStatus
from uuid import uuid4

from tests.unit.conftest import API, FILM_ID


def test_rate_film_creates_rating(client, auth, user_id):
    """Оценка сохраняется, а в ответе — зритель из токена, а не из тела запроса."""
    response = client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 9}, headers=auth)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body['rating'] == 9
    assert body['user_id'] == str(user_id)
    assert body['film_id'] == str(FILM_ID)


def test_rate_film_twice_replaces_rating(client, auth, likes_storage):
    """Повторная оценка заменяет прежнюю, а не добавляет вторую (ФТ-1)."""
    client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 3}, headers=auth)
    response = client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 10}, headers=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json()['rating'] == 10
    assert len(likes_storage.items) == 1


def test_rate_film_keeps_created_at(client, auth):
    """При изменении оценки время создания не меняется, а время правки — да."""
    first = client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 3}, headers=auth).json()
    second = client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 8}, headers=auth).json()

    assert second['created_at'] == first['created_at']
    assert second['updated_at'] >= first['updated_at']


def test_rate_film_rejects_rating_out_of_range(client, auth):
    """Оценка вне диапазона 0–10 отклоняется с 422."""
    response = client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 11}, headers=auth)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_validation_error_does_not_echo_input(client, auth):
    """Ответ 422 не повторяет присланные значения."""
    response = client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 42}, headers=auth)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert all('input' not in error for error in response.json()['detail'])


def test_rate_film_rejects_extra_fields(client, auth, user_id):
    """Лишнее поле в теле отклоняется: подменить зрителя через user_id нельзя."""
    response = client.put(
        f'{API}/films/{FILM_ID}/rating',
        json={'rating': 9, 'user_id': str(uuid4())},
        headers=auth,
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_unrate_film_removes_rating(client, auth, likes_storage):
    """Снятая оценка исчезает из хранилища, ответ — 204."""
    client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 7}, headers=auth)
    response = client.delete(f'{API}/films/{FILM_ID}/rating', headers=auth)

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert likes_storage.items == {}


def test_unrate_film_without_rating_returns_404(client, auth):
    """Снятие несуществующей оценки — 404 с машиночитаемым кодом."""
    response = client.delete(f'{API}/films/{FILM_ID}/rating', headers=auth)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'rating_not_found'


def test_my_rating_returns_own_rating(client, auth):
    """Своя оценка видна сразу после записи (НФТ-6)."""
    client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 6}, headers=auth)
    response = client.get(f'{API}/films/{FILM_ID}/rating/me', headers=auth)

    assert response.status_code == HTTPStatus.OK
    assert response.json()['rating'] == 6


def test_my_rating_404_when_not_rated(client, auth):
    """Если зритель не оценивал фильм — 404, а не нулевая оценка."""
    response = client.get(f'{API}/films/{FILM_ID}/rating/me', headers=auth)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'rating_not_found'


def test_film_rating_counts_likes_and_dislikes(client, make_token):
    """Агрегат считает лайки, дизлайки и среднюю оценку по всем зрителям."""
    for rating in (10, 8, 2):
        headers = {'Authorization': f'Bearer {make_token(uuid4())}'}
        client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': rating}, headers=headers)

    response = client.get(f'{API}/films/{FILM_ID}/rating')

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        'film_id': str(FILM_ID),
        'likes': 2,
        'dislikes': 1,
        'average_rating': 6.67,
    }


def test_film_rating_is_public(client):
    """Агрегат доступен без токена: он показывается в карточке фильма (ФТ-12)."""
    response = client.get(f'{API}/films/{FILM_ID}/rating')

    assert response.status_code == HTTPStatus.OK


def test_film_rating_without_votes_is_zero(client):
    """Фильм без единой оценки — не ошибка: нули и average_rating: null."""
    response = client.get(f'{API}/films/{uuid4()}/rating')

    assert response.status_code == HTTPStatus.OK
    assert response.json()['likes'] == 0
    assert response.json()['average_rating'] is None


def test_liked_films_returns_only_liked(client, auth):
    """В список понравившихся попадают оценки от 6 и выше."""
    client.put(f'{API}/films/{FILM_ID}/rating', json={'rating': 9}, headers=auth)
    client.put(f'{API}/films/{uuid4()}/rating', json={'rating': 2}, headers=auth)

    response = client.get(f'{API}/users/me/likes', headers=auth)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body['total'] == 1
    assert body['items'][0]['film_id'] == str(FILM_ID)


def test_liked_films_are_paginated(client, auth):
    """Страница не длиннее предела, а общее число записей видно в total."""
    for _ in range(4):
        client.put(f'{API}/films/{uuid4()}/rating', json={'rating': 8}, headers=auth)

    response = client.get(f'{API}/users/me/likes', headers=auth)

    body = response.json()
    assert body['total'] == 4
    assert len(body['items']) == 2  # PAGE_SIZE_DEFAULT
    assert body['page'] == 1


def test_page_size_is_capped(client, auth):
    """Запрошенный размер страницы обрезается настройкой сервиса."""
    for _ in range(5):
        client.put(f'{API}/films/{uuid4()}/rating', json={'rating': 8}, headers=auth)

    response = client.get(f'{API}/users/me/likes', params={'size': 100}, headers=auth)

    assert len(response.json()['items']) == 3  # PAGE_SIZE_MAX


def test_second_page_continues_list(client, auth):
    """Вторая страница продолжает список, а не повторяет первую."""
    for _ in range(4):
        client.put(f'{API}/films/{uuid4()}/rating', json={'rating': 8}, headers=auth)

    first = client.get(f'{API}/users/me/likes', params={'page': 1}, headers=auth).json()
    second = client.get(f'{API}/users/me/likes', params={'page': 2}, headers=auth).json()

    first_films = {item['film_id'] for item in first['items']}
    second_films = {item['film_id'] for item in second['items']}
    assert not first_films & second_films
    assert second['page'] == 2


def test_zero_page_is_rejected(client, auth):
    """Нулевая страница — ошибка параметра, а не молчаливая единица."""
    response = client.get(f'{API}/users/me/likes', params={'page': 0}, headers=auth)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
