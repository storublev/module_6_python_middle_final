"""Общие фикстуры функциональных тестов.

Тесты ходят к сервису по HTTP и проверяют то, что он записал, читая MongoDB
напрямую: кода сервиса они не импортируют и проверяют только видимое снаружи
поведение.

Данные каждый тест готовит и чистит сам — по своим идентификаторам фильма и
зрителя. Общую базу между тестами не чистим полностью: параллельный прогон
затирал бы чужие данные, а так тесты друг о друге не знают.
"""

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
import requests
from pymongo import MongoClient

from tests.functional.settings import settings

API = '/content/api/v1'
DOCS = '/content/api'
REQUEST_ID_HEADER = 'X-Request-Id'
REQUEST_ID = 'functional-tests'
COLLECTIONS = ('likes', 'film_ratings', 'bookmarks', 'reviews', 'review_votes')


@pytest.fixture(scope='session')
def http() -> Iterator[requests.Session]:
    """HTTP-клиент с идентификатором запроса, который в работе ставит nginx."""
    session = requests.Session()
    session.headers[REQUEST_ID_HEADER] = REQUEST_ID
    yield session
    session.close()


@pytest.fixture(scope='session')
def url() -> Callable[[str], str]:
    return lambda path: f'{settings.service_url}{path}'


@pytest.fixture(scope='session')
def mongo() -> Iterator[MongoClient]:
    """Прямое подключение к хранилищу: им тесты проверяют, что записал сервис."""
    client: MongoClient = MongoClient(settings.mongo_uri, uuidRepresentation='standard')
    yield client
    client.close()


@pytest.fixture
def database(mongo: MongoClient):
    return mongo[settings.mongo_database]


@pytest.fixture
def film_id() -> UUID:
    """Свой фильм на каждый тест: тесты не видят данных друг друга."""
    return uuid4()


@pytest.fixture
def user_id() -> UUID:
    return uuid4()


@pytest.fixture
def cleanup(database, film_id: UUID, user_id: UUID) -> Iterator[None]:
    """Убирает за тестом всё, что он мог записать по своему фильму и зрителю."""
    yield
    for collection in COLLECTIONS:
        database[collection].delete_many({'film_id': film_id})
        database[collection].delete_many({'user_id': user_id})


@pytest.fixture
def make_token() -> Callable[..., str]:
    """Выпускает токен так же, как сервис авторизации."""

    def factory(
        user: UUID | None = None,
        *,
        token_type: str = 'access',
        ttl: timedelta = timedelta(minutes=15),
        secret: str = settings.jwt_secret_key,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            'sub': str(user or uuid4()),
            'sid': str(uuid4()),
            'jti': uuid4().hex,
            'type': token_type,
            'iat': int(now.timestamp()),
            'exp': int((now + ttl).timestamp()),
        }
        return jwt.encode(payload, secret, algorithm='HS256')

    return factory


@pytest.fixture
def auth(make_token: Callable[..., str], user_id: UUID) -> dict[str, str]:
    """Заголовок с действующим токеном зрителя."""
    return {'Authorization': f'Bearer {make_token(user_id)}'}
