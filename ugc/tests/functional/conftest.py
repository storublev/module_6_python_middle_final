"""Общие фикстуры функциональных тестов.

Тесты ходят к сервису по HTTP и читают то, что он записал, прямо из Kafka:
кода сервиса они не импортируют и проверяют только видимое снаружи поведение.

Потребитель создаётся на каждый тест и встаёт в конец топика, поэтому тест
видит только свои события и не зависит от того, что записали предыдущие.
"""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

import jwt
import pytest
import requests
from kafka import KafkaConsumer, TopicPartition

from tests.functional.settings import settings

API = '/ugc/api/v1'
DOCS = '/ugc/api'
REQUEST_ID_HEADER = 'X-Request-Id'
REQUEST_ID = 'functional-tests'
# Сколько ждать событие из брокера: продюсер сервиса склеивает сообщения
# паузой в 10 мс, дальше всё зависит только от скорости брокера.
CONSUME_TIMEOUT = 15


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


@pytest.fixture
def consumer() -> Iterator[KafkaConsumer]:
    """Потребитель, стоящий в конце топика: тест видит только свои события."""
    client = KafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers.split(','),
        enable_auto_commit=False,
        value_deserializer=json.loads,
        consumer_timeout_ms=1000,
    )
    # topics() заставляет клиента сходить за метаданными: без этого
    # partitions_for_topic() вернёт None — не «партиций нет», а «ещё не знаю», —
    # и потребитель молча окажется подписан ни на что.
    client.topics()
    numbers = client.partitions_for_topic(settings.kafka_topic)
    assert numbers, f'топик {settings.kafka_topic} не найден в брокере'
    partitions = [TopicPartition(settings.kafka_topic, number) for number in numbers]
    client.assign(partitions)
    client.seek_to_end()
    # seek_to_end() только помечает, что позицию надо взять с конца, а берёт её
    # первый же poll() — то есть уже после того, как тест отправит событие, и
    # это событие окажется «до» конца и не прочитается. position() заставляет
    # разрешить позицию сейчас, пока в топике ещё ничего нашего нет.
    for partition in partitions:
        client.position(partition)
    yield client
    client.close()


@pytest.fixture
def read_events(consumer: KafkaConsumer) -> Callable[..., list[dict]]:
    """Читает из топика заданное число событий или столько, сколько успело прийти."""

    def read(count: int = 1, timeout: float = CONSUME_TIMEOUT) -> list[dict]:
        events: list[dict] = []
        deadline = monotonic() + timeout
        while len(events) < count and monotonic() < deadline:
            for records in consumer.poll(timeout_ms=500).values():
                events.extend(record.value for record in records)
        return events

    return read


@pytest.fixture(scope='session')
def make_token() -> Callable[..., str]:
    """Выпускает токен так же, как сервис авторизации."""

    def factory(
        user: UUID | None = None,
        *,
        token_type: str = 'access',
        ttl: timedelta = timedelta(minutes=15),
        secret: str = settings.jwt_secret_key,
    ) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                'sub': str(user or uuid4()),
                'sid': str(uuid4()),
                'jti': uuid4().hex,
                'type': token_type,
                'iat': int(now.timestamp()),
                'exp': int((now + ttl).timestamp()),
            },
            secret,
            algorithm='HS256',
        )

    return factory


@pytest.fixture
def user_id() -> UUID:
    return uuid4()


@pytest.fixture
def headers(make_token: Callable[..., str], user_id: UUID) -> dict[str, str]:
    return {'Authorization': f'Bearer {make_token(user_id)}'}


@pytest.fixture
def session_id() -> UUID:
    return uuid4()


@pytest.fixture
def make_event(session_id: UUID) -> Callable[..., dict]:
    """Собирает событие нужного типа; по умолчанию — клик."""

    def factory(event_type: str = 'click', **fields) -> dict:
        event = {
            'event_type': event_type,
            'event_id': str(uuid4()),
            'session_id': str(session_id),
            'occurred_at': datetime.now(UTC).isoformat(),
            'client': {'platform': 'web', 'device': 'pytest'},
        }
        event.update(_defaults(event_type))
        event.update(fields)
        return event

    return factory


def _defaults(event_type: str) -> dict:
    return {
        'click': {'element_type': 'film_card', 'page': '/catalog', 'film_id': str(uuid4())},
        'page_view': {'page': '/catalog', 'duration_ms': 4200},
        'quality_changed': {
            'film_id': str(uuid4()),
            'quality_from': '720p',
            'quality_to': '1080p',
            'position_ms': 120000,
        },
        'video_completed': {'film_id': str(uuid4()), 'watched_ratio': 0.98, 'duration_ms': 2140000},
        'search_filters_applied': {'query': 'нолан', 'filters': {'genre': 'sci-fi'}, 'results_count': 12},
    }.get(event_type, {})
