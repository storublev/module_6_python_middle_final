"""Рецензии: публикация, удаление, голоса за полезность и сортировки списка."""

from http import HTTPStatus
from uuid import uuid4

from tests.unit.conftest import API, FILM_ID

TEXT = 'Отличный фильм: держит до самого финала.'


def review_of(client, auth, text: str = TEXT, rating: int | None = 8, film=FILM_ID):
    """Публикует рецензию и отдаёт её тело — иначе каждый тест начинался бы с трёх строк."""
    body = {'text': text}
    if rating is not None:
        body['rating'] = rating
    return client.post(f'{API}/films/{film}/reviews', json=body, headers=auth)


def test_publish_review_returns_201(client, auth, user_id):
    """Опубликованная рецензия возвращается целиком, автор — из токена."""
    response = review_of(client, auth)

    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body['text'] == TEXT
    assert body['rating'] == 8
    assert body['user_id'] == str(user_id)
    assert body['useful'] == 0 and body['useless'] == 0


def test_publish_review_without_rating(client, auth):
    """Рецензию можно написать, не оценивая фильм."""
    response = review_of(client, auth, rating=None)

    assert response.status_code == HTTPStatus.CREATED
    assert response.json()['rating'] is None


def test_second_review_of_same_film_is_rejected(client, auth):
    """Вторая рецензия того же зрителя на тот же фильм — 409."""
    review_of(client, auth)
    response = review_of(client, auth, text='Передумал: фильм так себе.')

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()['code'] == 'review_already_exists'


def test_empty_review_is_rejected(client, auth):
    """Пустой текст рецензии не принимается."""
    response = review_of(client, auth, text='')

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_too_long_review_is_rejected(client, auth):
    """Рецензия длиннее предела не принимается: это защита хранилища."""
    response = review_of(client, auth, text='а' * 10_001)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_author_can_delete_own_review(client, auth):
    """Автор удаляет свою рецензию, ответ — 204."""
    review_id = review_of(client, auth).json()['review_id']

    response = client.delete(f'{API}/reviews/{review_id}', headers=auth)

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert client.get(f'{API}/films/{FILM_ID}/reviews').json()['total'] == 0


def test_stranger_cannot_delete_review(client, auth, make_token):
    """Чужая рецензия отвечает 403, а не 404: она существует, но не ваша."""
    review_id = review_of(client, auth).json()['review_id']
    stranger = {'Authorization': f'Bearer {make_token(uuid4())}'}

    response = client.delete(f'{API}/reviews/{review_id}', headers=stranger)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()['code'] == 'not_review_author'


def test_delete_unknown_review_returns_404(client, auth):
    """Удаление несуществующей рецензии — 404."""
    response = client.delete(f'{API}/reviews/{uuid4()}', headers=auth)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'review_not_found'


def test_vote_increases_useful(client, auth, make_token):
    """Голос «полезно» увеличивает счётчик полезности."""
    review_id = review_of(client, auth).json()['review_id']
    voter = {'Authorization': f'Bearer {make_token(uuid4())}'}

    response = client.put(f'{API}/reviews/{review_id}/vote', json={'useful': True}, headers=voter)

    assert response.status_code == HTTPStatus.OK
    assert response.json()['useful'] == 1
    assert response.json()['useless'] == 0


def test_repeated_vote_replaces_previous(client, auth, make_token):
    """Повторный голос заменяет прежний, а не добавляет второй (ФТ-7)."""
    review_id = review_of(client, auth).json()['review_id']
    voter = {'Authorization': f'Bearer {make_token(uuid4())}'}

    client.put(f'{API}/reviews/{review_id}/vote', json={'useful': True}, headers=voter)
    response = client.put(f'{API}/reviews/{review_id}/vote', json={'useful': False}, headers=voter)

    body = response.json()
    assert body['useful'] == 0
    assert body['useless'] == 1


def test_same_vote_twice_changes_nothing(client, auth, make_token):
    """Тот же голос второй раз ничего не меняет."""
    review_id = review_of(client, auth).json()['review_id']
    voter = {'Authorization': f'Bearer {make_token(uuid4())}'}

    client.put(f'{API}/reviews/{review_id}/vote', json={'useful': True}, headers=voter)
    response = client.put(f'{API}/reviews/{review_id}/vote', json={'useful': True}, headers=voter)

    assert response.json()['useful'] == 1


def test_vote_for_unknown_review_returns_404(client, auth):
    """Голос за несуществующую рецензию — 404."""
    response = client.put(f'{API}/reviews/{uuid4()}/vote', json={'useful': True}, headers=auth)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'review_not_found'


def test_reviews_list_is_public(client, auth):
    """Список рецензий виден без токена (ФТ-12)."""
    review_of(client, auth)

    response = client.get(f'{API}/films/{FILM_ID}/reviews')

    assert response.status_code == HTTPStatus.OK
    assert response.json()['total'] == 1


def test_reviews_sorted_by_useful(client, auth, make_token):
    """Сортировка «самые полезные» ставит наверх рецензию с большим числом голосов."""
    first = review_of(client, auth).json()['review_id']
    second_author = {'Authorization': f'Bearer {make_token(uuid4())}'}
    second = review_of(client, second_author, text='Вторая рецензия на тот же фильм.').json()['review_id']

    voter = {'Authorization': f'Bearer {make_token(uuid4())}'}
    client.put(f'{API}/reviews/{second}/vote', json={'useful': True}, headers=voter)

    items = client.get(f'{API}/films/{FILM_ID}/reviews', params={'sort': 'most_useful'}).json()['items']

    assert [item['review_id'] for item in items] == [second, first]


def test_reviews_sorted_by_rating(client, auth, make_token):
    """Сортировка по оценке автора ставит наверх рецензию с высшей оценкой."""
    low = review_of(client, auth, rating=3).json()['review_id']
    other = {'Authorization': f'Bearer {make_token(uuid4())}'}
    high = review_of(client, other, text='Вторая рецензия.', rating=10).json()['review_id']

    items = client.get(f'{API}/films/{FILM_ID}/reviews', params={'sort': 'highest_rating'}).json()['items']

    assert [item['review_id'] for item in items] == [high, low]


def test_unknown_sort_is_rejected(client):
    """Неизвестный порядок сортировки — ошибка параметра."""
    response = client.get(f'{API}/films/{FILM_ID}/reviews', params={'sort': 'random'})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_reviews_of_other_film_are_not_mixed(client, auth, make_token):
    """Рецензии на другой фильм в список не попадают."""
    review_of(client, auth)
    other_author = {'Authorization': f'Bearer {make_token(uuid4())}'}
    review_of(client, other_author, film=uuid4())

    assert client.get(f'{API}/films/{FILM_ID}/reviews').json()['total'] == 1
