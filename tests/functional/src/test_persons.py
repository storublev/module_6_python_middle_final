"""Эндпоинты /api/v1/persons: персона, её фильмы, список персон, кеш.

Поиск по персонам проверяется в test_search.py.
"""

from http import HTTPStatus

import pytest

from tests.functional.testdata.es_mapping import MOVIES_INDEX, PERSONS_INDEX
from tests.functional.testdata.factories import film_short, make_film, make_person, make_ref, new_id, person_view
from tests.functional.testdata.validation import INVALID_PAGINATION, INVALID_UUIDS, VALID_PAGINATION_EDGES


@pytest.fixture
async def filmography(es_write_data):
    """Персона с фильмами в разных ролях и фильм, где она не участвовала."""
    ref = make_ref('Ann Smith')
    acted = make_film(title='Acted', imdb_rating=7.0, actors=[ref])
    wrote = make_film(title='Wrote', imdb_rating=9.0, writers=[ref])
    directed = make_film(title='Directed', imdb_rating=5.0, directors=[ref])
    all_roles = make_film(title='All roles', imdb_rating=8.0, actors=[ref], writers=[ref], directors=[ref])
    other = make_film(title='Other', imdb_rating=9.9, actors=[make_ref('Bob Brown')])

    person = make_person('Ann Smith', films=[
        (acted, ['actor']),
        (wrote, ['writer']),
        (directed, ['director']),
        (all_roles, ['actor', 'writer', 'director']),
    ])
    person['id'] = ref['id']

    await es_write_data(MOVIES_INDEX, [acted, wrote, directed, all_roles, other])
    await es_write_data(PERSONS_INDEX, [person])
    return person, [acted, wrote, directed, all_roles]


# Персона

async def test_person_details(filmography, make_get_request):
    person, _ = filmography

    response = await make_get_request(f'/persons/{person["id"]}')

    assert response.status == HTTPStatus.OK
    assert response.body == person_view(person)


async def test_person_without_films(es_write_data, make_get_request):
    person = make_person('Nobody')
    await es_write_data(PERSONS_INDEX, [person])

    response = await make_get_request(f'/persons/{person["id"]}/')

    assert response.status == HTTPStatus.OK
    assert response.body == {'uuid': person['id'], 'full_name': 'Nobody', 'films': []}


async def test_person_not_found(es_write_data, make_get_request):
    await es_write_data(PERSONS_INDEX, [make_person()])

    response = await make_get_request(f'/persons/{new_id()}')

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.body == {'detail': 'person not found'}


@pytest.mark.parametrize('person_id', INVALID_UUIDS)
async def test_person_details_invalid_uuid(make_get_request, person_id):
    response = await make_get_request(f'/persons/{person_id}')

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


# Фильмы персоны

async def test_person_films_all_roles(filmography, make_get_request):
    person, films = filmography

    response = await make_get_request(f'/persons/{person["id"]}/film')

    assert response.status == HTTPStatus.OK
    by_rating = sorted(films, key=lambda film: film['imdb_rating'], reverse=True)
    assert response.body == [film_short(film) for film in by_rating]


async def test_person_films_sort_asc(filmography, make_get_request):
    person, films = filmography

    response = await make_get_request(f'/persons/{person["id"]}/film', {'sort': 'imdb_rating'})

    assert [film['imdb_rating'] for film in response.body] == sorted(film['imdb_rating'] for film in films)


async def test_person_films_pagination(filmography, make_get_request):
    person, _ = filmography

    first = await make_get_request(f'/persons/{person["id"]}/film', {'page_size': 3})
    second = await make_get_request(f'/persons/{person["id"]}/film', {'page_size': 3, 'page_number': 2})

    assert [film['title'] for film in first.body] == ['Wrote', 'All roles', 'Acted']
    assert [film['title'] for film in second.body] == ['Directed']


async def test_person_films_empty(es_write_data, make_get_request):
    person = make_person('Nobody')
    await es_write_data(PERSONS_INDEX, [person])
    await es_write_data(MOVIES_INDEX, [make_film()])

    response = await make_get_request(f'/persons/{person["id"]}/film')

    assert response.status == HTTPStatus.OK
    assert response.body == []


async def test_person_films_unknown_person(es_write_data, make_get_request):
    # Фильм ссылается на персону, которой нет в индексе persons.
    ref = make_ref('Ghost')
    await es_write_data(MOVIES_INDEX, [make_film(actors=[ref])])

    response = await make_get_request(f'/persons/{ref["id"]}/film')

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.body == {'detail': 'person not found'}


@pytest.mark.parametrize('person_id', INVALID_UUIDS)
async def test_person_films_invalid_uuid(make_get_request, person_id):
    response = await make_get_request(f'/persons/{person_id}/film')

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.parametrize(
    'params',
    [
        *INVALID_PAGINATION,
        pytest.param({'sort': 'title'}, id='sort-unknown-field'),
    ],
)
async def test_person_films_validation(filmography, make_get_request, params):
    person, _ = filmography

    response = await make_get_request(f'/persons/{person["id"]}/film', params)

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


# Список персон

async def test_person_list_sorted_by_name(es_write_data, make_get_request):
    persons = [make_person(name) for name in ('Zoe Adams', 'Ann Smith', 'Bob Brown')]
    await es_write_data(PERSONS_INDEX, persons)

    response = await make_get_request('/persons')

    assert response.status == HTTPStatus.OK
    assert response.body == [person_view(person) for person in sorted(persons, key=lambda p: p['full_name'])]


@pytest.mark.parametrize(
    'params, expected_length',
    [
        pytest.param({}, 50, id='default-page'),
        pytest.param({'page_size': 7}, 7, id='page-size'),
        pytest.param({'page_number': 2}, 10, id='second-page'),
        pytest.param({'page_number': 3}, 0, id='page-after-last'),
    ],
)
async def test_person_list_pagination(es_write_data, make_get_request, params, expected_length):
    await es_write_data(PERSONS_INDEX, [make_person(f'Person {number:03}') for number in range(60)])

    response = await make_get_request('/persons', params)

    assert response.status == HTTPStatus.OK
    assert len(response.body) == expected_length


@pytest.mark.parametrize('params', VALID_PAGINATION_EDGES)
async def test_person_list_pagination_edges_are_valid(make_get_request, params):
    response = await make_get_request('/persons', params)

    assert response.status == HTTPStatus.OK


@pytest.mark.parametrize('params', INVALID_PAGINATION)
async def test_person_list_validation(make_get_request, params):
    response = await make_get_request('/persons', params)

    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY


# Кеш

async def test_person_details_cached(es_write_data, es_delete_data, flush_cache, make_get_request):
    person = make_person()
    await es_write_data(PERSONS_INDEX, [person])
    await make_get_request(f'/persons/{person["id"]}')

    await es_delete_data(PERSONS_INDEX, [person])
    cached = await make_get_request(f'/persons/{person["id"]}')

    assert cached.status == HTTPStatus.OK
    assert cached.body == person_view(person)

    await flush_cache()
    after_flush = await make_get_request(f'/persons/{person["id"]}')
    assert after_flush.status == HTTPStatus.NOT_FOUND


async def test_person_films_cached(filmography, es_delete_data, flush_cache, make_get_request):
    person, films = filmography
    first = await make_get_request(f'/persons/{person["id"]}/film')

    await es_delete_data(MOVIES_INDEX, films)
    cached = await make_get_request(f'/persons/{person["id"]}/film')

    assert cached.body == first.body
    assert len(cached.body) == len(films)

    await flush_cache()
    after_flush = await make_get_request(f'/persons/{person["id"]}/film')
    assert after_flush.body == []


async def test_person_list_cached(es_write_data, es_delete_data, flush_cache, make_get_request):
    persons = [make_person('Ann Smith'), make_person('Bob Brown')]
    await es_write_data(PERSONS_INDEX, persons)
    await make_get_request('/persons')

    await es_delete_data(PERSONS_INDEX, persons)
    cached = await make_get_request('/persons')

    assert cached.body == [person_view(person) for person in persons]

    await flush_cache()
    after_flush = await make_get_request('/persons')
    assert after_flush.body == []
