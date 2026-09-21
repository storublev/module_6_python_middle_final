"""Общие фикстуры: событие из брокера и собранный перенос."""

from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
from uuid import uuid4

import pytest

from core.memory import MemoryWatch
from services.pipeline import EventPipeline, RetryPolicy
from storage.resilience import CircuitBreaker
from tests.unit.fakes import FakeSink, FakeSource

# Повторы без пауз: в тестах ждать нечего, время подменено.
POLICY = RetryPolicy(retries=2, base=0.01, cap=0.01)


@pytest.fixture
def make_event() -> Callable[..., dict]:
    """Событие в том виде, в каком его кладёт в брокер сервис приёма."""

    def factory(event_type: str = 'click', **fields) -> dict:
        event = {
            'event_id': str(uuid4()),
            'event_type': event_type,
            'session_id': str(uuid4()),
            'user_id': str(uuid4()),
            'occurred_at': '2026-09-21T19:04:11+03:00',
            'received_at': datetime.now(UTC).isoformat(),
            'client': {'platform': 'web', 'device': 'pytest', 'app_version': '1.0'},
        }
        event.update(_defaults(event_type))
        event.update(fields)
        return event

    return factory


def _defaults(event_type: str) -> dict:
    return {
        'click': {'element_type': 'film_card', 'page': '/catalog', 'film_id': str(uuid4())},
        'page_view': {'page': '/catalog', 'referrer': '/', 'duration_ms': 4200},
        'quality_changed': {
            'film_id': str(uuid4()),
            'quality_from': '720p',
            'quality_to': '1080p',
            'position_ms': 120000,
        },
        'video_completed': {'film_id': str(uuid4()), 'watched_ratio': 0.98, 'duration_ms': 2140000},
        'search_filters_applied': {'query': 'нолан', 'filters': {'genre': 'sci-fi'}, 'results_count': 12},
    }.get(event_type, {})


@pytest.fixture
def memory() -> MemoryWatch:
    return MemoryWatch(report_every=1, limit_mb=4096)


@pytest.fixture
def make_pipeline(memory: MemoryWatch) -> Callable[..., EventPipeline]:
    """Собирает перенос на источнике и приёмнике в памяти, без настоящих пауз.

    Часы прерывателя идут вперёд на минуту за обращение: иначе разомкнутый
    прерыватель никогда бы не дошёл до пробной попытки, и тест завис бы.
    `stop_after_sleeps` останавливает перенос после заданного числа пауз —
    так проверяется поведение при бесконечно недоступном хранилище.
    """

    def factory(
        source: FakeSource,
        sink: FakeSink,
        *,
        breaker_failures: int = 5,
        stop_after_sleeps: int | None = None,
    ) -> EventPipeline:
        clock = count(step=60)
        sleeps = 0

        def sleeper(_seconds: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if stop_after_sleeps and sleeps >= stop_after_sleeps:
                pipeline.stop()

        pipeline = EventPipeline(
            source=source,
            sink=sink,
            memory=memory,
            policy=POLICY,
            breaker=CircuitBreaker(breaker_failures, reset_timeout=30, clock=lambda: float(next(clock))),
            breaker_pause=0.01,
            sleeper=sleeper,
        )
        return pipeline

    return factory
