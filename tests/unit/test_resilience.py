"""Прерыватель обращений к внешнему сервису."""

import pytest

from storage.resilience import CircuitBreaker

FAILURES = 3
RESET_TIMEOUT = 30.0


class Clock:
    """Часы, которыми управляет тест: ждать настоящие секунды не нужно."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def breaker(clock: Clock) -> CircuitBreaker:
    return CircuitBreaker(failures=FAILURES, reset_timeout=RESET_TIMEOUT, clock=clock)


def test_closed_breaker_allows_requests(breaker):
    """Пока сбоев не было, запросы проходят."""
    assert breaker.allows()


def test_single_failures_do_not_open_breaker(breaker):
    """Сбоев меньше порога — прерыватель закрыт: случайная ошибка не отключает сервис."""
    for _ in range(FAILURES - 1):
        breaker.record_failure()

    assert breaker.allows()


def test_failures_in_a_row_open_breaker(breaker):
    """Сбои подряд до порога размыкают прерыватель: запросы больше не уходят."""
    for _ in range(FAILURES):
        breaker.record_failure()

    assert not breaker.allows()


def test_success_resets_failure_count(breaker):
    """Успешный ответ обнуляет счётчик: считаются только сбои подряд."""
    for _ in range(FAILURES - 1):
        breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()

    assert breaker.allows()


def test_probe_is_allowed_after_reset_timeout(breaker, clock):
    """Через reset_timeout проходит пробный запрос — сервис мог подняться."""
    for _ in range(FAILURES):
        breaker.record_failure()
    clock.now += RESET_TIMEOUT

    assert breaker.allows()


def test_only_one_probe_passes_per_timeout(breaker, clock):
    """Пока пробный запрос не ответил, остальные ждут: упавший сервис не заваливают."""
    for _ in range(FAILURES):
        breaker.record_failure()
    clock.now += RESET_TIMEOUT
    breaker.allows()

    assert not breaker.allows()


def test_successful_probe_closes_breaker(breaker, clock):
    """Удачный пробный запрос закрывает прерыватель: работа продолжается как обычно."""
    for _ in range(FAILURES):
        breaker.record_failure()
    clock.now += RESET_TIMEOUT
    breaker.allows()
    breaker.record_success()

    assert breaker.allows()
    assert not breaker.is_open


def test_failed_probe_opens_breaker_again(breaker, clock):
    """Неудачный пробный запрос снова открывает прерыватель на полный таймаут."""
    for _ in range(FAILURES):
        breaker.record_failure()
    clock.now += RESET_TIMEOUT
    breaker.allows()
    breaker.record_failure()
    clock.now += RESET_TIMEOUT - 1

    assert not breaker.allows()
