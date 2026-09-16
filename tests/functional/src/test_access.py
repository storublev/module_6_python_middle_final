"""Доступ к подписочным фильмам: кому что видно в /api/v1/films.

Проверяется связка с настоящим сервисом авторизации: API обменивает токен на
ответ о праве `films.subscription` и по нему решает, что отдать. Фильмы,
которые ETL пометил `access_level=subscription`, открыты только подписчику.
"""

from http import HTTPStatus

import pytest

from tests.functional.testdata.es_mapping import MOVIES_INDEX, PERSONS_INDEX
from tests.functional.testdata.factories import (
    SUBSCRIPTION,
    film_short,
    make_film,
    make_person,
    make_ref,
    new_id,
)

GARBAGE_TOKEN = 'not.a.token'  # noqa: S105 — заведомо непригодный токен, а не секрет


@pytest.fixture
def subscription_film():
    return make_film(title='Fresh Blockbuster', access_level=SUBSCRIPTION)


@pytest.fixture
def public_film():
    return make_film(title='Old Classic')


async def test_public_film_is_open_to_anonymous(es_write_data, make_get_request, public_film):
    """Публичный фильм отдаётся без токена: каталог остаётся открытым."""
    await es_write_data(MOVIES_INDEX, [public_film])

    response = await make_get_request(f'/films/{public_film["id"]}')

    assert response.status == HTTPStatus.OK


async def test_subscription_film_is_closed_to_anonymous(es_write_data, make_get_request, subscription_film):
    """Подписочный фильм анонимному пользователю не отдаётся."""
    await es_write_data(MOVIES_INDEX, [subscription_film])

    response = await make_get_request(f'/films/{subscription_film["id"]}')

    assert response.status == HTTPStatus.FORBIDDEN


async def test_refusal_explains_the_reason(es_write_data, make_get_request, subscription_film):
    """В отказе сказано, что фильм по подписке, — клиенту есть что показать пользователю."""
    await es_write_data(MOVIES_INDEX, [subscription_film])

    response = await make_get_request(f'/films/{subscription_film["id"]}')

    assert response.body == {'detail': 'film is available by subscription only'}


async def test_subscription_film_is_closed_without_subscription(
    es_write_data, make_get_request, subscription_film, viewer_token,
):
    """Вошедшему пользователю без подписки подписочный фильм тоже закрыт."""
    await es_write_data(MOVIES_INDEX, [subscription_film])

    response = await make_get_request(f'/films/{subscription_film["id"]}', token=viewer_token)

    assert response.status == HTTPStatus.FORBIDDEN


async def test_subscription_film_is_open_to_subscriber(
    es_write_data, make_get_request, subscription_film, subscriber_token,
):
    """Подписчику подписочный фильм отдаётся целиком."""
    await es_write_data(MOVIES_INDEX, [subscription_film])

    response = await make_get_request(f'/films/{subscription_film["id"]}', token=subscriber_token)

    assert response.status == HTTPStatus.OK
    assert response.body['title'] == 'Fresh Blockbuster'


async def test_missing_film_is_not_found_for_anonymous(make_get_request):
    """Несуществующий фильм — 404, а не «нужна подписка».

    Иначе по коду ответа можно было бы узнать, какие фильмы есть в каталоге.
    """
    response = await make_get_request(f'/films/{new_id()}')

    assert response.status == HTTPStatus.NOT_FOUND


async def test_garbage_token_is_rejected(es_write_data, make_get_request, public_film):
    """Непригодный токен не считается анонимным запросом: клиент узнаёт, что токен не принят."""
    await es_write_data(MOVIES_INDEX, [public_film])

    response = await make_get_request(f'/films/{public_film["id"]}', token=GARBAGE_TOKEN)

    assert response.status == HTTPStatus.UNAUTHORIZED


async def test_list_hides_subscription_films_from_anonymous(
    es_write_data, make_get_request, public_film, subscription_film,
):
    """Список анонимного пользователя состоит только из публичных фильмов."""
    await es_write_data(MOVIES_INDEX, [public_film, subscription_film])

    response = await make_get_request('/films')

    assert response.body == [film_short(public_film)]


async def test_list_shows_subscription_films_to_subscriber(
    es_write_data, make_get_request, public_film, subscription_film, subscriber_token,
):
    """Подписчик видит в списке и публичные, и подписочные фильмы."""
    await es_write_data(MOVIES_INDEX, [public_film, subscription_film])

    response = await make_get_request('/films', token=subscriber_token)

    assert {film['uuid'] for film in response.body} == {public_film['id'], subscription_film['id']}


async def test_search_hides_subscription_films_from_anonymous(
    es_write_data, make_get_request, subscription_film,
):
    """Поиск не показывает анониму подписочный фильм, даже если тот подходит запросу."""
    await es_write_data(MOVIES_INDEX, [subscription_film])

    response = await make_get_request('/films/search', {'query': 'Blockbuster'})

    assert response.body == []


async def test_search_finds_subscription_films_for_subscriber(
    es_write_data, make_get_request, subscription_film, subscriber_token,
):
    """Подписчику поиск подписочный фильм находит."""
    await es_write_data(MOVIES_INDEX, [subscription_film])

    response = await make_get_request('/films/search', {'query': 'Blockbuster'}, token=subscriber_token)

    assert response.body == [film_short(subscription_film)]


async def test_person_films_hide_subscription_films_from_anonymous(es_write_data, make_get_request):
    """Фильмы персоны тоже отбираются по доступу: через них закрытое не утекает."""
    # Фильмы персоны ищутся по её id внутри документов фильмов, поэтому она
    # должна быть вписана в состав участников, а не только знать о них.
    ref = make_ref('Ann Smith')
    public = make_film(title='Old Classic', actors=[ref])
    subscription = make_film(title='Fresh Blockbuster', actors=[ref], access_level=SUBSCRIPTION)
    person = make_person('Ann Smith', films=[(public, ['actor']), (subscription, ['actor'])])
    person['id'] = ref['id']
    await es_write_data(MOVIES_INDEX, [public, subscription])
    await es_write_data(PERSONS_INDEX, [person])

    response = await make_get_request(f'/persons/{person["id"]}/film')

    assert response.body == [film_short(public)]


async def test_cache_does_not_leak_subscription_films(
    es_write_data, make_get_request, public_film, subscription_film, subscriber_token,
):
    """Выдача подписчика не попадает в кеш анонимного пользователя: ключи различаются."""
    await es_write_data(MOVIES_INDEX, [public_film, subscription_film])
    await make_get_request('/films', token=subscriber_token)

    response = await make_get_request('/films')

    assert response.body == [film_short(public_film)]
