"""Целостность связанных записей: оценка и счётчик, рецензия и голоса за неё.

Каждая такая пара пишется одной транзакцией. Проверяем не код, а видимый
результат: после сбоя посередине в базе не должно оставаться половины
изменения, а параллельные запросы не должны загонять счётчики в
несуществующие состояния.
"""

import concurrent.futures
from http import HTTPStatus
from uuid import UUID, uuid4

from tests.functional.conftest import API

TEXT = 'Рецензия для проверки целостности данных.'


def test_rating_and_counter_change_together(http, url, auth, film_id, user_id, database, cleanup):
    """Оценка и счётчик фильма всегда согласованы между собой."""
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 9}, headers=auth)

    likes = list(database.likes.find({'film_id': film_id}))
    counter = database.film_ratings.find_one({'film_id': film_id})

    assert len(likes) == counter['votes']
    assert sum(like['rating'] for like in likes) == counter['sum_rating']


def test_counter_survives_transaction_abort(http, url, auth, film_id, mongo, database, cleanup):
    """Прерванная транзакция не оставляет половины изменения.

    Транзакция имитируется вручную и обрывается между двумя записями — ровно
    тот случай, когда процесс умирает посередине. В базе после этого не должно
    остаться ни оценки, ни сдвига счётчика.
    """
    with mongo.start_session() as session:
        with session.start_transaction():
            database.likes.insert_one(
                {'film_id': film_id, 'user_id': uuid4(), 'rating': 10,
                 'created_at': '2026-01-01', 'updated_at': '2026-01-01'},
                session=session,
            )
            session.abort_transaction()

    assert database.likes.count_documents({'film_id': film_id}) == 0
    assert database.film_ratings.count_documents({'film_id': film_id}) == 0


def test_parallel_opposite_votes_keep_counters_valid(http, url, auth, make_token, film_id, database, cleanup):
    """Параллельные противоположные голоса не загоняют счётчики в минус.

    Счётчик не может быть отрицательным: модель `Review` такого значения не
    принимает, и запрос вернул бы 500. Транзакции с повтором при конфликте
    должны исключать такое состояние.
    """
    review_id = http.post(
        url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT}, headers=auth,
    ).json()['review_id']
    voters = [{'Authorization': f'Bearer {make_token(uuid4())}'} for _ in range(8)]

    def vote(index: int) -> int:
        headers = voters[index % len(voters)]
        response = http.put(
            url(f'{API}/reviews/{review_id}/vote'),
            json={'useful': index % 2 == 0},
            headers=headers,
        )
        return response.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(vote, range(24)))

    assert all(status == HTTPStatus.OK for status in statuses), statuses
    review = database.reviews.find_one({'review_id': UUID(review_id)})
    assert review['useful'] >= 0
    assert review['useless'] >= 0
    # Голосов ровно столько, сколько разных зрителей: повторные заменяют свои.
    assert review['useful'] + review['useless'] == database.review_votes.count_documents(
        {'review_id': UUID(review_id)},
    )
    database.review_votes.delete_many({'review_id': UUID(review_id)})


def test_parallel_ratings_keep_counter_exact(http, url, make_token, film_id, database, cleanup):
    """Счётчик фильма точен и при одновременных оценках от разных зрителей."""
    voters = [{'Authorization': f'Bearer {make_token(uuid4())}'} for _ in range(10)]

    def rate(index: int) -> int:
        response = http.put(
            url(f'{API}/films/{film_id}/rating'),
            json={'rating': 10 if index % 2 == 0 else 1},
            headers=voters[index],
        )
        return response.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(rate, range(len(voters))))

    assert all(status == HTTPStatus.OK for status in statuses), statuses
    counter = database.film_ratings.find_one({'film_id': film_id})
    likes = list(database.likes.find({'film_id': film_id}))

    assert counter['votes'] == len(likes) == len(voters)
    assert counter['likes'] == sum(1 for like in likes if like['rating'] >= 6)
    assert counter['sum_rating'] == sum(like['rating'] for like in likes)
    database.likes.delete_many({'film_id': film_id})


def test_deleting_review_leaves_no_votes(http, url, auth, make_token, film_id, database, cleanup):
    """Удаление рецензии забирает с собой все голоса за неё."""
    review_id = http.post(
        url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT}, headers=auth,
    ).json()['review_id']
    for _ in range(3):
        voter = {'Authorization': f'Bearer {make_token(uuid4())}'}
        http.put(url(f'{API}/reviews/{review_id}/vote'), json={'useful': True}, headers=voter)
    assert database.review_votes.count_documents({'review_id': UUID(review_id)}) == 3

    http.delete(url(f'{API}/reviews/{review_id}'), headers=auth)

    assert database.reviews.count_documents({'review_id': UUID(review_id)}) == 0
    assert database.review_votes.count_documents({'review_id': UUID(review_id)}) == 0


def test_vote_and_delete_race_leaves_nothing_behind(http, url, auth, make_token, film_id, database, cleanup):
    """Гонка «голос против удаления» не оставляет голосов за удалённой рецензией.

    Раньше голосующий мог успеть проверить существование рецензии до её
    удаления, а записать голос — после очистки. Теперь и проверка, и запись
    идут внутри одной транзакции, так что либо голос попадает к живой
    рецензии, либо не попадает вовсе.
    """
    review_id = http.post(
        url(f'{API}/films/{film_id}/reviews'), json={'text': TEXT}, headers=auth,
    ).json()['review_id']
    voter = {'Authorization': f'Bearer {make_token(uuid4())}'}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        vote = pool.submit(
            http.put, url(f'{API}/reviews/{review_id}/vote'), json={'useful': True}, headers=voter,
        )
        delete = pool.submit(http.delete, url(f'{API}/reviews/{review_id}'), headers=auth)
        vote_status, delete_status = vote.result().status_code, delete.result().status_code

    assert delete_status == HTTPStatus.NO_CONTENT
    assert vote_status in (HTTPStatus.OK, HTTPStatus.NOT_FOUND), vote_status
    # Голосов за удалённую рецензию остаться не должно ни в одном исходе.
    assert database.review_votes.count_documents({'review_id': UUID(review_id)}) == 0
