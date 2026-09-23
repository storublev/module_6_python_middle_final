"""Рецензии на живом стеке: публикация, голоса, сортировки, удаление."""

from http import HTTPStatus
from uuid import uuid4

from tests.functional.conftest import API

TEXT = 'Рецензия функционального теста: фильм держит до самого финала.'


def test_review_is_stored(http, url, auth, film_id, user_id, database, cleanup):
    """Опубликованная рецензия лежит в MongoDB и возвращается клиенту."""
    response = http.post(url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT, 'rating': 9}, headers=auth)

    assert response.status_code == HTTPStatus.CREATED
    document = database.reviews.find_one({'film_id': film_id, 'user_id': user_id})
    assert document['text'] == TEXT
    assert document['rating'] == 9


def test_second_review_of_same_film_conflicts(http, url, auth, film_id, cleanup):
    """Уникальный индекс не даёт написать вторую рецензию на тот же фильм."""
    http.post(url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT}, headers=auth)
    response = http.post(url(f'{API}/films/{film_id}/reviews'), json={'text': 'Вторая'}, headers=auth)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()['code'] == 'review_already_exists'


def test_vote_changes_counters(http, url, auth, make_token, film_id, database, cleanup):
    """Голос за полезность двигает счётчики рецензии и сохраняется отдельной записью."""
    review_id = http.post(
        url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT}, headers=auth,
    ).json()['review_id']
    voter_id = uuid4()
    voter = {'Authorization': f'Bearer {make_token(voter_id)}'}

    response = http.put(url(f'{API}/reviews/{review_id}/vote'), json={'useful': True}, headers=voter)

    assert response.json()['useful'] == 1
    assert database.review_votes.find_one({'user_id': voter_id})['useful'] is True
    database.review_votes.delete_many({'user_id': voter_id})


def test_repeated_vote_replaces_previous(http, url, auth, make_token, film_id, database, cleanup):
    """Повторный голос заменяет прежний: у зрителя один голос на рецензию."""
    review_id = http.post(
        url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT}, headers=auth,
    ).json()['review_id']
    voter_id = uuid4()
    voter = {'Authorization': f'Bearer {make_token(voter_id)}'}

    http.put(url(f'{API}/reviews/{review_id}/vote'), json={'useful': True}, headers=voter)
    response = http.put(url(f'{API}/reviews/{review_id}/vote'), json={'useful': False}, headers=voter)

    assert response.json() == {**response.json(), 'useful': 0, 'useless': 1}
    assert database.review_votes.count_documents({'user_id': voter_id}) == 1
    database.review_votes.delete_many({'user_id': voter_id})


def test_reviews_sorted_by_usefulness(http, url, make_token, film_id, database, cleanup):
    """Сортировка «самые полезные» ставит наверх рецензию с голосами."""
    authors = [{'Authorization': f'Bearer {make_token(uuid4())}'} for _ in range(2)]
    first = http.post(url(f'{API}/films/{film_id}/reviews'),
                      json={'text': 'Первая рецензия теста.'}, headers=authors[0]).json()['review_id']
    second = http.post(url(f'{API}/films/{film_id}/reviews'),
                       json={'text': 'Вторая рецензия теста.'}, headers=authors[1]).json()['review_id']
    voter = {'Authorization': f'Bearer {make_token(uuid4())}'}
    http.put(url(f'{API}/reviews/{second}/vote'), json={'useful': True}, headers=voter)

    items = http.get(url(f'{API}/films/{film_id}/reviews'), params={'sort': 'most_useful'}).json()['items']

    assert [item['review_id'] for item in items] == [second, first]
    database.reviews.delete_many({'film_id': film_id})
    database.review_votes.delete_many({'review_id': {'$in': [first, second]}})


def test_author_deletes_review_with_votes(http, url, auth, make_token, film_id, database, cleanup):
    """Удаление рецензии убирает и голоса за неё: мусор в базе не остаётся."""
    review_id = http.post(
        url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT}, headers=auth,
    ).json()['review_id']
    voter = {'Authorization': f'Bearer {make_token(uuid4())}'}
    http.put(url(f'{API}/reviews/{review_id}/vote'), json={'useful': True}, headers=voter)

    response = http.delete(url(f'{API}/reviews/{review_id}'), headers=auth)

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert database.reviews.count_documents({'film_id': film_id}) == 0
    assert database.review_votes.count_documents({'review_id': review_id}) == 0


def test_stranger_cannot_delete_review(http, url, auth, make_token, film_id, cleanup):
    """Чужую рецензию удалить нельзя — 403."""
    review_id = http.post(
        url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT}, headers=auth,
    ).json()['review_id']
    stranger = {'Authorization': f'Bearer {make_token(uuid4())}'}

    response = http.delete(url(f'{API}/reviews/{review_id}'), headers=stranger)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()['code'] == 'not_review_author'


def test_reviews_list_is_public(http, url, auth, film_id, cleanup):
    """Список рецензий доступен без токена."""
    http.post(url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT}, headers=auth)

    response = http.get(url(f'{API}/films/{film_id}/reviews'))

    assert response.status_code == HTTPStatus.OK
    assert response.json()['total'] == 1
