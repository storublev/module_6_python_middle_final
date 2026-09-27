"""Листание списков: сколько работы стоит страница.

Предел размера страницы ограничивает объём ответа, но не объём работы базы:
чтобы отдать страницу со смещением, MongoDB проходит все предшествующие
записи, а точный подсчёт — всю выборку. Эти тесты следят за тем, что оба
ограничителя на месте.
"""

from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from uuid import uuid4

import pytest

from tests.functional.conftest import API
from tests.functional.settings import settings


@pytest.fixture
def film_with_reviews(database, film_id):
    """Фильм, у которого рецензий заметно больше предела точного подсчёта."""
    count = settings.exact_count_limit * 2
    now = datetime.now(timezone.utc)
    database.reviews.insert_many([
        {
            'review_id': uuid4(), 'film_id': film_id, 'user_id': uuid4(),
            'text': f'рецензия номер {number}', 'rating': number % 11,
            'useful': number, 'useless': 0, 'created_at': now - timedelta(minutes=number),
        }
        for number in range(count)
    ])
    yield film_id, count
    database.reviews.delete_many({'film_id': film_id})


def test_deep_page_is_rejected(http, url, film_id):
    """Страница за пределом глубины отклоняется, а не выполняется молча."""
    page = settings.max_page_offset // settings.page_size_default + 2

    response = http.get(url(f'{API}/films/{film_id}/reviews'), params={'page': page})

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()['code'] == 'page_too_deep'
    # В ответе видно и предел, и текущее смещение: по нему понятно, что делать.
    assert str(settings.max_page_offset) in response.json()['detail']


def test_last_allowed_page_still_works(http, url, film_with_reviews):
    """Последняя разрешённая страница отдаётся как обычно."""
    film_id, _ = film_with_reviews
    page = settings.max_page_offset // settings.page_size_default + 1

    response = http.get(url(f'{API}/films/{film_id}/reviews'), params={'page': page})

    assert response.status_code == HTTPStatus.OK


def test_deep_page_is_rejected_for_every_list(http, url, auth):
    """Предел глубины действует на все списки, а не только на рецензии."""
    page = settings.max_page_offset // settings.page_size_default + 2

    for path, headers in (
        (f'{API}/users/me/likes', auth),
        (f'{API}/users/me/bookmarks', auth),
    ):
        response = http.get(url(path), params={'page': page}, headers=headers)

        assert response.status_code == HTTPStatus.BAD_REQUEST, path
        assert response.json()['code'] == 'page_too_deep', path


def test_total_is_capped_instead_of_exact_count(http, url, film_with_reviews):
    """Общее число не считается точно за пределом: это работа по всей выборке.

    Рецензий вдвое больше предела, а в `total` приходит сам предел — клиенту
    достаточно знать, что записей «больше чем столько».
    """
    film_id, count = film_with_reviews

    body = http.get(url(f'{API}/films/{film_id}/reviews')).json()

    assert count > settings.exact_count_limit, 'иначе тест ничего не проверяет'
    assert body['total'] == settings.exact_count_limit
    assert len(body['items']) == settings.page_size_default


def test_small_list_keeps_exact_total(http, url, auth, film_id, database, cleanup):
    """На небольших списках общее число остаётся точным."""
    http.put(url(f'{API}/films/{film_id}/rating'), json={'rating': 9}, headers=auth)

    body = http.get(url(f'{API}/users/me/likes'), headers=auth).json()

    assert body['total'] == 1
