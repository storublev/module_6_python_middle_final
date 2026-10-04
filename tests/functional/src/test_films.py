"""Эндпоинты /api/v1/films: карточка фильма, список фильмов, кеш."""

from http import HTTPStatus

import pytest

from tests.functional.testdata.es_mapping import MOVIES_INDEX
from tests.functional.testdata.factories import (
    MOVIE,
    TV_SHOW,
    film_full,
    film_short,
    make_film,
    make_films,
    make_ref,
    new_id,
)
from tests.functional.testdata.validation import INVALID_PAGINATION, INVALID_UUIDS, VALID_PAGINATION_EDGES


async def test_film_details(es_write_data, make_get_request):
    film = make_film(
        title='The Star',
        imdb_rating=8.5,
        description='New World',
        genres=[make_ref('Action'), make_ref('Sci-Fi')],
        actors=[make_ref('Ann'), make_ref('Bob')],
        writers=[make_ref('Ben')],
        directors=[make_ref('Stan')],
    )
    await es_write_data(MOVIES_INDEX, [film, make_film(title='Other')])

    response = await make_get_request(f'/films/{film["id"]}')

    assert response.status == HTTPStatus.OK
    assert response.body == film_full(film)


async def test_film_details_without_optional_fields(es_write_data, make_get_request):
    film = make_film(imdb_rating=None, description=None, poster_url=None, imdb_id=None, title_ru=None,
                     description_ru=None)
    await es_write_data(MOVIES_INDEX, [film])

    response = await make_get_request(f'/films/{film["id"]}')

    assert response.status == HTTPStatus.OK
    assert response.body == film_full(film)


async def test_film_details_trailing_slash(es_write_data, make_get_request):
    film = make_film()
    await es_write_data(MOVIES_INDEX, [film])

    response = await make_get_request(f'/films/{film["id"]}/')

    assert response.status == HTTPStatus.OK
    assert response.body['uuid'] == film['id']


async def test_film_not_found(es_write_data, make_get_request):
    await es_write_data(MOVIES_INDEX, [make_film()])

    response = await make_get_request(f'/films/{new_id()}')

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.body == {'detail': 'film not found'}


@pytest.mark.parametrize('film_id', INVALID_UUIDS)
async def test_film_details_invalid_uuid(make_get_request, film_id):
    response = await make_get_request(f'/films/{film_id}')

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


# Список фильмов

async def test_film_list_default_page(es_write_data, make_get_request):
    films = make_films(60)
    await es_write_data(MOVIES_INDEX, films)

    response = await make_get_request('/films')

    assert response.status == HTTPStatus.OK
    assert len(response.body) == 50
    assert set(response.body[0]) == {'uuid', 'title', 'imdb_rating', 'type', 'poster_url', 'title_ru'}


@pytest.mark.parametrize(
    'params, expected_length',
    [
        pytest.param({'page_size': 1}, 1, id='one'),
        pytest.param({'page_size': 10}, 10, id='ten'),
        pytest.param({'page_size': 100}, 60, id='all-fit-on-page'),
        pytest.param({'page_number': 2}, 10, id='second-page-rest'),
        pytest.param({'page_number': 3}, 0, id='page-after-last'),
        pytest.param({'page_number': 2, 'page_size': 25}, 25, id='second-page-full'),
    ],
)
async def test_film_list_pagination(es_write_data, make_get_request, params, expected_length):
    await es_write_data(MOVIES_INDEX, make_films(60))

    response = await make_get_request('/films', params)

    assert response.status == HTTPStatus.OK
    assert len(response.body) == expected_length


async def test_film_list_pages_do_not_overlap(es_write_data, make_get_request):
    # Одинаковый рейтинг у всех фильмов: порядок держится на втором ключе сортировки.
    films = make_films(30, imdb_rating=7.0)
    await es_write_data(MOVIES_INDEX, films)

    pages = [await make_get_request('/films', {'page_number': number, 'page_size': 10}) for number in (1, 2, 3)]

    ids = [film['uuid'] for page in pages for film in page.body]
    assert sorted(ids) == sorted(film['id'] for film in films)


@pytest.mark.parametrize(
    'sort, reverse',
    [
        pytest.param(None, True, id='default-desc'),
        pytest.param('-imdb_rating', True, id='desc'),
        pytest.param('imdb_rating', False, id='asc'),
    ],
)
async def test_film_list_sort_by_rating(es_write_data, make_get_request, sort, reverse):
    films = [make_film(imdb_rating=rating) for rating in (5.1, 9.3, 7.7, 1.2, 8.8)]
    await es_write_data(MOVIES_INDEX, films)

    response = await make_get_request('/films', {'sort': sort} if sort else None)

    assert response.status == HTTPStatus.OK
    ratings = [film['imdb_rating'] for film in response.body]
    assert ratings == sorted(ratings, reverse=reverse)
    assert len(ratings) == len(films)


async def test_film_list_filter_by_genre(es_write_data, make_get_request):
    comedy, drama = make_ref('Comedy'), make_ref('Drama')
    comedies = make_films(3, genres=[comedy])
    both = make_film(genres=[comedy, drama])
    dramas = make_films(2, genres=[drama])
    await es_write_data(MOVIES_INDEX, [*comedies, both, *dramas])

    response = await make_get_request('/films', {'genre': comedy['id']})

    assert response.status == HTTPStatus.OK
    assert sorted(film['uuid'] for film in response.body) == sorted(film['id'] for film in [*comedies, both])


async def test_film_list_filter_by_type(es_write_data, make_get_request):
    """Фильтр по типу оставляет только полнометражные фильмы — их и можно бронировать."""
    movies = make_films(3, film_type=MOVIE)
    shows = make_films(2, film_type=TV_SHOW)
    await es_write_data(MOVIES_INDEX, [*movies, *shows])

    response = await make_get_request('/films', {'type': 'movie'})

    assert response.status == HTTPStatus.OK
    assert sorted(film['uuid'] for film in response.body) == sorted(film['id'] for film in movies)
    assert {film['type'] for film in response.body} == {MOVIE}


async def test_film_list_unknown_genre(es_write_data, make_get_request):
    await es_write_data(MOVIES_INDEX, make_films(3, genres=[make_ref('Comedy')]))

    response = await make_get_request('/films', {'genre': new_id()})

    assert response.status == HTTPStatus.OK
    assert response.body == []


async def test_film_list_empty_index(make_get_request):
    response = await make_get_request('/films')

    assert response.status == HTTPStatus.OK
    assert response.body == []


async def test_film_list_trailing_slash(es_write_data, make_get_request):
    await es_write_data(MOVIES_INDEX, make_films(2))

    response = await make_get_request('/films/')

    assert response.status == HTTPStatus.OK
    assert len(response.body) == 2


@pytest.mark.parametrize('params', VALID_PAGINATION_EDGES)
async def test_film_list_pagination_edges_are_valid(make_get_request, params):
    response = await make_get_request('/films', params)

    assert response.status == HTTPStatus.OK


@pytest.mark.parametrize(
    'params',
    [
        *INVALID_PAGINATION,
        pytest.param({'sort': 'title'}, id='sort-unknown-field'),
        pytest.param({'sort': '+imdb_rating'}, id='sort-unknown-direction'),
        pytest.param({'genre': 'comedy'}, id='genre-not-uuid'),
        pytest.param({'type': 'cartoon'}, id='type-unknown'),
    ],
)
async def test_film_list_validation(make_get_request, params):
    response = await make_get_request('/films', params)

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


# Кеш

async def test_film_details_cached(es_write_data, es_delete_data, flush_cache, make_get_request):
    film = make_film()
    await es_write_data(MOVIES_INDEX, [film])
    first = await make_get_request(f'/films/{film["id"]}')

    await es_delete_data(MOVIES_INDEX, [film])
    cached = await make_get_request(f'/films/{film["id"]}')

    assert cached.status == HTTPStatus.OK
    assert cached.body == first.body == film_full(film)

    await flush_cache()
    after_flush = await make_get_request(f'/films/{film["id"]}')
    assert after_flush.status == HTTPStatus.NOT_FOUND


async def test_film_not_found_is_not_cached(es_write_data, make_get_request):
    film = make_film()
    missing = await make_get_request(f'/films/{film["id"]}')
    assert missing.status == HTTPStatus.NOT_FOUND

    await es_write_data(MOVIES_INDEX, [film])
    found = await make_get_request(f'/films/{film["id"]}')

    assert found.status == HTTPStatus.OK


async def test_film_list_cached(es_write_data, es_delete_data, flush_cache, make_get_request):
    films = make_films(5)
    await es_write_data(MOVIES_INDEX, films)
    first = await make_get_request('/films')

    await es_delete_data(MOVIES_INDEX, films)
    cached = await make_get_request('/films')

    assert cached.status == HTTPStatus.OK
    assert cached.body == first.body
    assert sorted(film['uuid'] for film in cached.body) == sorted(film_short(film)['uuid'] for film in films)

    await flush_cache()
    after_flush = await make_get_request('/films')
    assert after_flush.body == []


async def test_film_corrupted_cache_is_ignored(es_write_data, redis_client, make_get_request):
    film = make_film()
    await es_write_data(MOVIES_INDEX, [film])
    await make_get_request(f'/films/{film["id"]}')
    await make_get_request('/films')

    # Записи могут испортиться или остаться от прошлой версии API.
    async for key in redis_client.scan_iter():
        await redis_client.set(key, b'{"broken": ')

    details = await make_get_request(f'/films/{film["id"]}')
    films = await make_get_request('/films')

    assert details.status == HTTPStatus.OK
    assert details.body == film_full(film)
    assert films.body == [film_short(film)]


async def test_film_list_cache_depends_on_params(es_write_data, make_get_request):
    await es_write_data(MOVIES_INDEX, make_films(5))
    await make_get_request('/films', {'page_size': 2})

    response = await make_get_request('/films', {'page_size': 3})

    assert len(response.body) == 3


# Хранилище недоступно

async def test_films_index_missing(drop_index, make_get_request):
    await drop_index(MOVIES_INDEX)

    response = await make_get_request('/films')

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.body == {'detail': 'service temporarily unavailable'}
