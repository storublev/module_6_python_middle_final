"""Поиск: /api/v1/films/search и /api/v1/persons/search."""

from http import HTTPStatus

import pytest

from tests.functional.testdata.es_mapping import MOVIES_INDEX, PERSONS_INDEX
from tests.functional.testdata.factories import film_short, make_film, make_films, make_person, person_view
from tests.functional.testdata.validation import INVALID_PAGINATION

INVALID_QUERY = [
    pytest.param({}, id='query-missing'),
    pytest.param({'query': ''}, id='query-empty'),
]


# Поиск фильмов

@pytest.mark.parametrize(
    'query, expected_length',
    [
        pytest.param('The Star', 50, id='phrase'),
        pytest.param('star', 50, id='word-any-case'),
        pytest.param('Starr', 50, id='typo'),
        pytest.param('Mashed potato', 0, id='not-found'),
    ],
)
async def test_film_search(es_write_data, make_get_request, query, expected_length):
    await es_write_data(MOVIES_INDEX, make_films(60, title='The Star'))

    response = await make_get_request('/films/search', {'query': query})

    assert response.status == HTTPStatus.OK
    assert len(response.body) == expected_length


async def test_film_search_finds_only_matching(es_write_data, make_get_request):
    wanted = make_film(title='Star Trek', description='Space')
    await es_write_data(MOVIES_INDEX, [wanted, make_film(title='Casablanca', description='Morocco')])

    response = await make_get_request('/films/search', {'query': 'trek'})

    assert response.status == HTTPStatus.OK
    assert response.body == [film_short(wanted)]


async def test_film_search_by_description(es_write_data, make_get_request):
    wanted = make_film(title='Solaris', description='A psychologist is sent to a space station')
    await es_write_data(MOVIES_INDEX, [wanted, make_film(title='Casablanca', description='Morocco')])

    response = await make_get_request('/films/search', {'query': 'psychologist'})

    assert response.body == [film_short(wanted)]


async def test_film_search_title_is_more_relevant(es_write_data, make_get_request):
    in_description = make_film(title='Solaris', description='Ocean planet', imdb_rating=9.9)
    in_title = make_film(title='Ocean', description='Heist', imdb_rating=1.0)
    await es_write_data(MOVIES_INDEX, [in_description, in_title])

    response = await make_get_request('/films/search', {'query': 'ocean'})

    # Сортировка по релевантности, а не по рейтингу: совпадение в названии весомее.
    assert [film['uuid'] for film in response.body] == [in_title['id'], in_description['id']]


@pytest.mark.parametrize(
    'params, expected_length',
    [
        pytest.param({'page_size': 1}, 1, id='one'),
        pytest.param({'page_size': 15}, 15, id='fifteen'),
        pytest.param({'page_size': 100}, 60, id='all-fit-on-page'),
        pytest.param({'page_number': 2}, 10, id='second-page-rest'),
        pytest.param({'page_number': 3}, 0, id='page-after-last'),
    ],
)
async def test_film_search_returns_n_records(es_write_data, make_get_request, params, expected_length):
    await es_write_data(MOVIES_INDEX, make_films(60, title='The Star'))

    response = await make_get_request('/films/search', {'query': 'star', **params})

    assert response.status == HTTPStatus.OK
    assert len(response.body) == expected_length


@pytest.mark.parametrize('params', [*INVALID_QUERY, *(
    pytest.param({'query': 'star', **param.values[0]}, id=param.id) for param in INVALID_PAGINATION
)])
async def test_film_search_validation(make_get_request, params):
    response = await make_get_request('/films/search', params)

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


async def test_film_search_cached(es_write_data, es_delete_data, flush_cache, make_get_request):
    films = make_films(3, title='The Star')
    await es_write_data(MOVIES_INDEX, films)
    first = await make_get_request('/films/search', {'query': 'star'})

    await es_delete_data(MOVIES_INDEX, films)
    cached = await make_get_request('/films/search', {'query': 'star'})

    assert cached.body == first.body
    assert len(cached.body) == 3

    await flush_cache()
    after_flush = await make_get_request('/films/search', {'query': 'star'})
    assert after_flush.body == []


async def test_film_search_cache_depends_on_query(es_write_data, make_get_request):
    star, war = make_film(title='The Star'), make_film(title='The War')
    await es_write_data(MOVIES_INDEX, [star, war])
    await make_get_request('/films/search', {'query': 'star'})

    response = await make_get_request('/films/search', {'query': 'war'})

    assert response.body == [film_short(war)]


# Поиск персон

@pytest.mark.parametrize(
    'query',
    [
        pytest.param('Ann Smith', id='full-name'),
        pytest.param('smith', id='last-name-any-case'),
        pytest.param('Smiht', id='typo'),
    ],
)
async def test_person_search(es_write_data, make_get_request, query):
    ann = make_person('Ann Smith')
    await es_write_data(PERSONS_INDEX, [ann, make_person('George Lucas')])

    response = await make_get_request('/persons/search', {'query': query})

    assert response.status == HTTPStatus.OK
    assert response.body == [person_view(ann)]


async def test_person_search_with_films(es_write_data, make_get_request):
    film = make_film()
    person = make_person('Harrison Ford', films=[(film, ['actor'])])
    await es_write_data(PERSONS_INDEX, [person])

    response = await make_get_request('/persons/search', {'query': 'harrison'})

    assert response.body == [person_view(person)]


async def test_person_search_not_found(es_write_data, make_get_request):
    await es_write_data(PERSONS_INDEX, [make_person('Ann Smith')])

    response = await make_get_request('/persons/search', {'query': 'Mashed potato'})

    assert response.status == HTTPStatus.OK
    assert response.body == []


@pytest.mark.parametrize(
    'params, expected_length',
    [
        pytest.param({}, 50, id='default-page'),
        pytest.param({'page_size': 5}, 5, id='five'),
        pytest.param({'page_number': 2}, 10, id='second-page-rest'),
    ],
)
async def test_person_search_returns_n_records(es_write_data, make_get_request, params, expected_length):
    await es_write_data(PERSONS_INDEX, [make_person('Ann Smith') for _ in range(60)])

    response = await make_get_request('/persons/search', {'query': 'smith', **params})

    assert len(response.body) == expected_length


@pytest.mark.parametrize('params', [*INVALID_QUERY, *(
    pytest.param({'query': 'smith', **param.values[0]}, id=param.id) for param in INVALID_PAGINATION
)])
async def test_person_search_validation(make_get_request, params):
    response = await make_get_request('/persons/search', params)

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


async def test_person_search_cached(es_write_data, es_delete_data, flush_cache, make_get_request):
    person = make_person('Ann Smith')
    await es_write_data(PERSONS_INDEX, [person])
    await make_get_request('/persons/search', {'query': 'smith'})

    await es_delete_data(PERSONS_INDEX, [person])
    cached = await make_get_request('/persons/search', {'query': 'smith'})

    assert cached.body == [person_view(person)]

    await flush_cache()
    after_flush = await make_get_request('/persons/search', {'query': 'smith'})
    assert after_flush.body == []
