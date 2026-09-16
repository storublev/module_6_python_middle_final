"""FilmService: как уровень доступа пользователя влияет на выдачу каталога."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import pytest

from models.film import AccessLevel
from services.access import Access
from services.base import Pagination
from services.cache import ModelCache
from services.errors import AccessCheckUnavailableError, SubscriptionRequiredError
from services.film import FilmService
from storage.base import Document, DocumentStorage, SearchRequest
from storage.cache import Cache

FILM_ID = UUID('2a090dde-f688-46fe-a9f4-b781a985275e')
PUBLIC_ONLY = Access((AccessLevel.PUBLIC,))
SUBSCRIBER = Access((AccessLevel.PUBLIC, AccessLevel.SUBSCRIPTION))
DEGRADED = Access((AccessLevel.PUBLIC,), degraded=True)
PAGE = Pagination(page_number=1, page_size=10)


class FakeStorage(DocumentStorage):
    """Хранилище в памяти: отдаёт заданный документ и запоминает поисковые запросы."""

    def __init__(self, document: Document | None = None) -> None:
        self.document = document
        self.requests: list[SearchRequest] = []

    async def get(self, index: str, doc_id: str, fields: Sequence[str]) -> Document | None:
        return self.document

    async def search(self, index: str, request: SearchRequest) -> list[Document]:
        self.requests.append(request)
        return []


class NoCache(Cache):
    """Кеш, который ничего не помнит: тесты проверяют сервис, а не кеширование."""

    async def get(self, key: str) -> bytes | None:
        return None

    async def set(self, key: str, value: bytes | str, expire: int) -> None:
        return None


def film_document(access_level: str) -> dict[str, Any]:
    return {'id': str(FILM_ID), 'title': 'The Matrix', 'access_level': access_level}


def build_service(document: dict[str, Any] | None = None) -> tuple[FilmService, FakeStorage]:
    storage = FakeStorage(document)
    return FilmService(storage, ModelCache(NoCache(), expire=60)), storage


def access_filter(request: SearchRequest) -> tuple[str, ...]:
    """Значения, которыми запрос ограничил уровень доступа."""
    return next(condition.values for condition in request.filters if condition.field == 'access_level')


async def test_public_film_is_open_to_anyone():
    """Публичный фильм отдаётся и без токена."""
    service, _ = build_service(film_document(AccessLevel.PUBLIC))

    film = await service.get_by_id(FILM_ID, PUBLIC_ONLY)

    assert film.title == 'The Matrix'


async def test_subscription_film_is_open_to_subscriber():
    """Подписочный фильм отдаётся тому, у кого есть право на подписку."""
    service, _ = build_service(film_document(AccessLevel.SUBSCRIPTION))

    film = await service.get_by_id(FILM_ID, SUBSCRIBER)

    assert film.title == 'The Matrix'


async def test_subscription_film_is_closed_without_subscription():
    """Без подписки подписочный фильм не отдаётся."""
    service, _ = build_service(film_document(AccessLevel.SUBSCRIPTION))

    with pytest.raises(SubscriptionRequiredError):
        await service.get_by_id(FILM_ID, PUBLIC_ONLY)


async def test_unverifiable_subscription_is_not_a_refusal():
    """Пока права не проверить, отказ — временный: у пользователя подписка могла быть."""
    service, _ = build_service(film_document(AccessLevel.SUBSCRIPTION))

    with pytest.raises(AccessCheckUnavailableError):
        await service.get_by_id(FILM_ID, DEGRADED)


async def test_public_film_is_served_while_auth_is_down():
    """Публичный фильм отдаётся и без работающего сервиса авторизации."""
    service, _ = build_service(film_document(AccessLevel.PUBLIC))

    assert await service.get_by_id(FILM_ID, DEGRADED) is not None


async def test_missing_film_is_not_found_before_access_is_judged():
    """Про несуществующий фильм отвечаем «нет», а не «нужна подписка»."""
    service, _ = build_service(document=None)

    assert await service.get_by_id(FILM_ID, PUBLIC_ONLY) is None


async def test_film_without_access_level_is_public():
    """Документ, проиндексированный до появления метки, считается публичным."""
    service, _ = build_service({'id': str(FILM_ID), 'title': 'The Matrix'})

    assert await service.get_by_id(FILM_ID, PUBLIC_ONLY) is not None


@pytest.mark.parametrize(
    'access, expected',
    [
        pytest.param(PUBLIC_ONLY, (AccessLevel.PUBLIC,), id='anonymous'),
        pytest.param(SUBSCRIBER, (AccessLevel.PUBLIC, AccessLevel.SUBSCRIPTION), id='subscriber'),
        pytest.param(DEGRADED, (AccessLevel.PUBLIC,), id='auth-unavailable'),
    ],
)
async def test_list_is_limited_to_available_levels(access, expected):
    """Список ограничен уровнями доступа пользователя."""
    service, storage = build_service()

    await service.get_list(PAGE, access, sort='-imdb_rating')

    assert access_filter(storage.requests[0]) == expected


async def test_search_is_limited_to_available_levels():
    """Поиск не показывает того, что пользователю недоступно."""
    service, storage = build_service()

    await service.search('matrix', PAGE, PUBLIC_ONLY)

    assert access_filter(storage.requests[0]) == (AccessLevel.PUBLIC,)


async def test_films_by_person_are_limited_to_available_levels():
    """Фильмы персоны тоже отбираются по доступу: иначе закрытое утекало бы через них."""
    service, storage = build_service()

    await service.get_by_person(uuid4(), PAGE, PUBLIC_ONLY, sort='-imdb_rating')

    assert access_filter(storage.requests[0]) == (AccessLevel.PUBLIC,)


async def test_different_access_levels_do_not_share_cache():
    """Запросы анонима и подписчика различаются, поэтому их выдача не смешается в кеше."""
    service, storage = build_service()

    await service.get_list(PAGE, PUBLIC_ONLY, sort='-imdb_rating')
    await service.get_list(PAGE, SUBSCRIBER, sort='-imdb_rating')

    assert storage.requests[0] != storage.requests[1]
