"""Прерыватель обращений к внешнему сервису (circuit breaker).

Когда сервис авторизации лежит, каждый вход ждёт таймаута и держит воркер
занятым. Прерыватель запоминает подряд идущие сбои и после порога перестаёт
ходить в сервис вовсе: запросы сразу получают отказ, а админка отвечает
формой с понятной ошибкой, а не зависает.

Состояния: closed (запросы идут) → open (после N сбоев подряд, на
reset_timeout секунд) → half-open (пропускается один пробный запрос: успех
закрывает прерыватель, сбой снова открывает его).

Счётчик живёт в процессе: у каждого воркера gunicorn он свой. Общего
состояния тут не нужно — порог маленький, и каждый воркер выясняет
недоступность за считанные запросы.
"""

import threading
from collections.abc import Callable
from time import monotonic


class CircuitBreaker:
    def __init__(self, failures: int, reset_timeout: float, clock: Callable[[], float] = monotonic) -> None:
        self._failures_threshold = failures
        self._reset_timeout = reset_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    def allows(self) -> bool:
        """Можно ли отправлять запрос: закрыт, либо открыт и пора пробовать снова."""
        with self._lock:
            if self._opened_at is None:
                return True
            now = self._clock()
            if now - self._opened_at >= self._reset_timeout:
                # Half-open: пропускаем один пробный запрос, а отсчёт паузы
                # начинаем заново — пока он не ответит, остальные ждут.
                # Прерыватель закроет только успешный ответ.
                self._opened_at = now
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._failures_threshold:
                # Пробный запрос из half-open тоже продлевает паузу.
                self._opened_at = self._clock()
