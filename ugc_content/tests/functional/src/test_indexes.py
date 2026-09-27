"""Индексы хранилища: проверяем не их наличие, а планы запросов.

Индекс, который есть, но не используется, хуже, чем его отсутствие: он берёт
плату с каждой записи и ничего не ускоряет. Поэтому тесты смотрят в
`explain`: какой план выбрала MongoDB и сколько работы он потребовал.
"""

from uuid import UUID, uuid4

import pytest

from tests.functional.conftest import API

LIKED_FROM = 6
# Оценок у зрителя заметно больше страницы: на трёх записях любой план
# выглядит одинаково хорошо.
RATINGS = 200
PAGE = 20


def stages_of(plan: dict) -> list[str]:
    """Разворачивает дерево выполнения в список этапов, от внешнего к внутреннему.

    План берётся из `cursor.explain()`: у синхронного курсора pymongo он
    всегда возвращается в режиме `allPlansExecution`, где статистика
    выполнения уже собрана.
    """
    stages, stage = [], plan['executionStats']['executionStages']
    while stage:
        stages.append(stage['stage'])
        stage = stage.get('inputStage')
    return stages


@pytest.fixture
def viewer_with_ratings(database, user_id: UUID):
    """Зритель, оценивший много фильмов: половина оценок — положительные."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    database.likes.insert_many([
        {
            'film_id': uuid4(),
            'user_id': user_id,
            'rating': number % 11,
            'created_at': now - timedelta(minutes=number),
            'updated_at': now,
        }
        for number in range(RATINGS)
    ])
    yield user_id
    database.likes.delete_many({'user_id': user_id})


def test_liked_films_query_needs_no_sort_stage(database, viewer_with_ratings):
    """Список понравившихся читается по индексу, без отдельной сортировки.

    Порядок полей в индексе решает всё: если поставить `rating` (условие
    диапазона) перед `created_at`, подходящие записи окажутся разбросаны по
    разным значениям оценки, индекс перестанет давать общий порядок по дате и
    в плане появится этап SORT — то есть база отсортирует выборку в памяти.
    """
    plan = database.likes.find(
        {'user_id': viewer_with_ratings, 'rating': {'$gte': LIKED_FROM}},
    ).sort('created_at', -1).limit(PAGE).explain()

    assert 'SORT' not in stages_of(plan), stages_of(plan)
    assert 'IXSCAN' in stages_of(plan), stages_of(plan)


def test_liked_films_query_reads_only_one_page(database, viewer_with_ratings):
    """Чтобы отдать страницу, база читает ровно страницу ключей, а не всю выборку."""
    plan = database.likes.find(
        {'user_id': viewer_with_ratings, 'rating': {'$gte': LIKED_FROM}},
    ).sort('created_at', -1).limit(PAGE).explain()
    stats = plan['executionStats']

    assert stats['nReturned'] == PAGE
    assert stats['totalKeysExamined'] == PAGE
    assert stats['totalDocsExamined'] == PAGE


def test_liked_index_is_partial(database):
    """Индекс понравившихся хранит только положительные оценки.

    Дизлайки в этой выборке не участвуют никогда, и держать их в индексе —
    значит платить за них при каждой записи.
    """
    index = next(i for i in database.likes.list_indexes() if i['name'] == 'like_user_liked')

    assert index.get('partialFilterExpression') == {'rating': {'$gte': LIKED_FROM}}
    assert list(index['key']) == ['user_id', 'created_at']


def test_film_rating_is_read_by_key(database, film_id):
    """Агрегат по фильму читается одной записью по ключу, а не подсчётом оценок."""
    database.film_ratings.insert_one({
        'film_id': film_id, 'likes': 3, 'dislikes': 1, 'sum_rating': 25, 'votes': 4,
    })

    plan = database.film_ratings.find({'film_id': film_id}).explain()

    assert 'COLLSCAN' not in stages_of(plan), stages_of(plan)
    assert plan['executionStats']['totalDocsExamined'] <= 1
    database.film_ratings.delete_many({'film_id': film_id})


def test_reviews_sorting_uses_index(database, film_id):
    """Каждая сортировка рецензий опирается на свой индекс, а не на сортировку в памяти."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    database.reviews.insert_many([
        {
            'review_id': uuid4(), 'film_id': film_id, 'user_id': uuid4(),
            'text': f'рецензия {number}', 'rating': number % 11,
            'useful': number, 'useless': 0, 'created_at': now - timedelta(minutes=number),
        }
        for number in range(50)
    ])

    for field in ('created_at', 'useful', 'rating'):
        plan = database.reviews.find({'film_id': film_id}).sort(field, -1).limit(PAGE).explain()
        assert 'SORT' not in stages_of(plan), (field, stages_of(plan))

    database.reviews.delete_many({'film_id': film_id})


def test_public_aggregate_endpoint_matches_storage(http, url, database, film_id):
    """Счётчик, прочитанный по индексу, — это то же самое, что отдаёт API."""
    database.film_ratings.insert_one({
        'film_id': film_id, 'likes': 2, 'dislikes': 1, 'sum_rating': 21, 'votes': 3,
    })

    body = http.get(url(f'{API}/films/{film_id}/rating')).json()

    assert body['likes'] == 2
    assert body['dislikes'] == 1
    assert body['average_rating'] == 7.0
    database.film_ratings.delete_many({'film_id': film_id})
