"""Повторы с паузой, прерыватель и наблюдение за памятью."""

import logging

import pytest

from core.memory import MemoryWatch, rss_mb
from storage.resilience import CircuitBreaker, retry_with_backoff


class Flaky:
    """Действие, которое отказывает заданное число раз, а потом срабатывает."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise ValueError('пока не получается')


def test_action_is_repeated_until_it_works() -> None:
    """Повторы продолжаются, пока действие не удастся."""
    action = Flaky(failures=2)
    pauses: list[float] = []

    retry_with_backoff(action, retries=3, base=1, cap=10, errors=(ValueError,), sleeper=pauses.append, rng=lambda: 0.5)

    assert action.calls == 3
    assert pauses == [1, 2]


def test_pause_grows_but_not_beyond_the_cap() -> None:
    """Пауза растёт вдвое, но не превышает предел."""
    pauses: list[float] = []

    with pytest.raises(ValueError, match='пока не получается'):
        retry_with_backoff(
            Flaky(failures=10), retries=4, base=1, cap=3, errors=(ValueError,),
            sleeper=pauses.append, rng=lambda: 0.5,
        )

    assert pauses == [1, 2, 3, 3]


def test_last_failure_is_raised() -> None:
    """Когда повторы кончились, ошибка выходит наружу — решать вызывающему."""
    with pytest.raises(ValueError, match='пока не получается'):
        retry_with_backoff(
            Flaky(failures=5), retries=1, base=0.01, cap=0.01, errors=(ValueError,), sleeper=lambda _: None,
        )


def test_pauses_are_spread_out() -> None:
    """К паузе добавляется разброс: экземпляры ETL не должны вернуться к хранилищу разом."""
    pauses: list[float] = []

    retry_with_backoff(
        Flaky(failures=1), retries=1, base=10, cap=10, errors=(ValueError,),
        sleeper=pauses.append, rng=lambda: 0.0,
    )

    assert pauses == [5]


def test_breaker_opens_after_failures_in_a_row() -> None:
    """После порога сбоев подряд прерыватель перестаёт пропускать попытки."""
    breaker = CircuitBreaker(failures=2, reset_timeout=30, clock=lambda: 0.0)

    breaker.record_failure()
    assert breaker.allows()
    breaker.record_failure()

    assert not breaker.allows()
    assert breaker.is_open


def test_breaker_lets_one_probe_through_after_the_pause() -> None:
    """После паузы проходит одна пробная попытка."""
    now = [0.0]
    breaker = CircuitBreaker(failures=1, reset_timeout=30, clock=lambda: now[0])
    breaker.record_failure()

    now[0] = 31
    assert breaker.allows()
    # Вторая подряд не проходит: ждём ответа пробной.
    assert not breaker.allows()


def test_success_closes_the_breaker() -> None:
    """Удачная попытка закрывает прерыватель и обнуляет счётчик сбоев."""
    breaker = CircuitBreaker(failures=1, reset_timeout=30, clock=lambda: 0.0)
    breaker.record_failure()

    breaker.record_success()

    assert breaker.allows()
    assert not breaker.is_open


def test_memory_is_measured_in_megabytes() -> None:
    """Замер памяти возвращает разумное число мегабайт, а не байты и не килобайты."""
    assert 1 < rss_mb() < 100_000


def test_memory_is_reported_every_n_batches(caplog) -> None:
    """Журнал пишется раз в заданное число пачек, а не после каждой."""
    watch = MemoryWatch(report_every=3, limit_mb=100_000)

    with caplog.at_level(logging.INFO, logger='core.memory'):
        for _ in range(6):
            watch.batch_done(rows=10)

    assert sum('Обработано пачек' in record.message for record in caplog.records) == 2


def test_exceeding_the_limit_is_a_warning_not_a_crash(caplog) -> None:
    """Превышение предела памяти попадает в журнал предупреждением, но перенос не роняет."""
    watch = MemoryWatch(report_every=1, limit_mb=0.001)

    with caplog.at_level(logging.WARNING, logger='core.memory'):
        watch.batch_done(rows=1)

    assert any('Память превысила предел' in record.message for record in caplog.records)
