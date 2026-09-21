"""Устойчивость к сбоям внешних систем: повторы с паузой и прерыватель.

Тот же приём, что в Async API (`src/storage/resilience.py`): у ETL обе стороны
внешние — брокер и хранилище, — и обе иногда лежат. Общее для них собрано
здесь, чтобы адаптеры не зависели друг от друга.
"""

import logging
import random
from collections.abc import Callable
from time import monotonic, sleep

logger = logging.getLogger(__name__)


def retry_with_backoff(
    action: Callable[[], None],
    *,
    retries: int,
    base: float,
    cap: float,
    errors: tuple[type[Exception], ...],
    sleeper: Callable[[float], None] = sleep,
    rng: Callable[[], float] = random.random,  # noqa: S311 — разброс пауз, а не криптография
) -> None:
    """Повторяет действие с экспоненциальной паузой: base * 2^n, но не больше cap.

    К паузе добавляется случайный разброс: иначе несколько экземпляров ETL,
    упавших на одном и том же сбое хранилища, вернутся к нему одновременно и
    добьют его вместе.

    Последняя неудача выходит наружу как есть — решать, что делать, должен
    вызывающий код.
    """
    for attempt in range(retries + 1):
        try:
            action()
            return
        except errors as error:
            if attempt == retries:
                raise
            pause = min(cap, base * 2 ** attempt) * (0.5 + rng())
            logger.warning(
                'Попытка %d из %d не удалась (%s), повтор через %.1f с',
                attempt + 1, retries + 1, error, pause,
            )
            sleeper(pause)


class CircuitBreaker:
    """Прерыватель обращений к внешней системе (circuit breaker).

    Когда хранилище лежит, каждая попытка вставки ждёт таймаута, а вместе с
    ней стоит и перенос. Прерыватель считает сбои подряд и после порога
    перестаёт пропускать попытки: ETL сразу узнаёт, что идти некуда, и ждёт, а
    события тем временем копятся в Kafka — там они живут неделю.

    Состояния: closed (запросы идут) → open (после N сбоев подряд, на
    reset_timeout секунд) → half-open (проходит один пробный запрос: успех
    закрывает прерыватель, сбой открывает его снова).
    """

    def __init__(self, failures: int, reset_timeout: float, clock: Callable[[], float] = monotonic) -> None:
        self._failures_threshold = failures
        self._reset_timeout = reset_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """Открыт ли прерыватель прямо сейчас — для журнала и тестов."""
        return self._opened_at is not None

    def allows(self) -> bool:
        """Можно ли пробовать: прерыватель закрыт либо пора пробовать снова."""
        if self._opened_at is None:
            return True
        now = self._clock()
        if now - self._opened_at >= self._reset_timeout:
            # Half-open: пропускаем одну пробную попытку, а отсчёт паузы
            # начинаем заново. Закроет прерыватель только успешная вставка.
            self._opened_at = now
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._failures_threshold:
            self._opened_at = self._clock()
