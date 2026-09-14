"""Эндпоинты /api/v1/genres: жанр, список жанров, кеш."""

from http import HTTPStatus

import pytest

from tests.functional.testdata.es_mapping import GENRES_INDEX
from tests.functional.testdata.factories import genre_view, make_genre, new_id
from tests.functional.testdata.validation import INVALID_PAGINATION, INVALID_UUIDS, VALID_PAGINATION_EDGES


async def test_genre_details(es_write_data, make_get_request):
    genre = make_genre('Comedy', description='Funny films')
    await es_write_data(GENRES_INDEX, [genre, make_genre('Drama')])

    response = await make_get_request(f'/genres/{genre["id"]}')

    assert response.status == HTTPStatus.OK
    assert response.body == genre_view(genre)


async def test_genre_details_trailing_slash(es_write_data, make_get_request):
    genre = make_genre()
    await es_write_data(GENRES_INDEX, [genre])

    response = await make_get_request(f'/genres/{genre["id"]}/')

    assert response.status == HTTPStatus.OK
    assert response.body == genre_view(genre)


async def test_genre_not_found(es_write_data, make_get_request):
    await es_write_data(GENRES_INDEX, [make_genre()])

    response = await make_get_request(f'/genres/{new_id()}')

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.body == {'detail': 'genre not found'}


@pytest.mark.parametrize('genre_id', INVALID_UUIDS)
async def test_genre_details_invalid_uuid(make_get_request, genre_id):
    response = await make_get_request(f'/genres/{genre_id}')

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


# Список жанров

async def test_genre_list_sorted_by_name(es_write_data, make_get_request):
    genres = [make_genre(name) for name in ('Western', 'Action', 'Sci-Fi', 'Comedy', 'Drama')]
    await es_write_data(GENRES_INDEX, genres)

    response = await make_get_request('/genres')

    assert response.status == HTTPStatus.OK
    assert response.body == [genre_view(genre) for genre in sorted(genres, key=lambda genre: genre['name'])]


@pytest.mark.parametrize(
    'params, expected_names',
    [
        pytest.param({'page_size': 2}, ['A', 'B'], id='first-page'),
        pytest.param({'page_size': 2, 'page_number': 2}, ['C', 'D'], id='second-page'),
        pytest.param({'page_size': 2, 'page_number': 3}, ['E'], id='last-page'),
        pytest.param({'page_size': 2, 'page_number': 4}, [], id='page-after-last'),
    ],
)
async def test_genre_list_pagination(es_write_data, make_get_request, params, expected_names):
    await es_write_data(GENRES_INDEX, [make_genre(name) for name in 'EDCBA'])

    response = await make_get_request('/genres', params)

    assert response.status == HTTPStatus.OK
    assert [genre['name'] for genre in response.body] == expected_names


async def test_genre_list_default_page_size(es_write_data, make_get_request):
    await es_write_data(GENRES_INDEX, [make_genre(f'Genre {number:03}') for number in range(60)])

    response = await make_get_request('/genres')

    assert response.status == HTTPStatus.OK
    assert len(response.body) == 50


async def test_genre_list_empty(make_get_request):
    response = await make_get_request('/genres')

    assert response.status == HTTPStatus.OK
    assert response.body == []


@pytest.mark.parametrize('params', VALID_PAGINATION_EDGES)
async def test_genre_list_pagination_edges_are_valid(make_get_request, params):
    response = await make_get_request('/genres', params)

    assert response.status == HTTPStatus.OK


@pytest.mark.parametrize('params', INVALID_PAGINATION)
async def test_genre_list_validation(make_get_request, params):
    response = await make_get_request('/genres', params)

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


# Кеш

async def test_genre_details_cached(es_write_data, es_delete_data, flush_cache, make_get_request):
    genre = make_genre()
    await es_write_data(GENRES_INDEX, [genre])
    await make_get_request(f'/genres/{genre["id"]}')

    await es_delete_data(GENRES_INDEX, [genre])
    cached = await make_get_request(f'/genres/{genre["id"]}')

    assert cached.status == HTTPStatus.OK
    assert cached.body == genre_view(genre)

    await flush_cache()
    after_flush = await make_get_request(f'/genres/{genre["id"]}')
    assert after_flush.status == HTTPStatus.NOT_FOUND


async def test_genre_list_cached(es_write_data, es_delete_data, flush_cache, make_get_request):
    genres = [make_genre(name) for name in ('Action', 'Drama')]
    await es_write_data(GENRES_INDEX, genres)
    await make_get_request('/genres')

    await es_delete_data(GENRES_INDEX, genres)
    cached = await make_get_request('/genres')

    assert cached.body == [genre_view(genre) for genre in genres]

    await flush_cache()
    after_flush = await make_get_request('/genres')
    assert after_flush.body == []


# Хранилище недоступно

async def test_genres_index_missing(drop_index, make_get_request):
    await drop_index(GENRES_INDEX)

    response = await make_get_request(f'/genres/{new_id()}')

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
