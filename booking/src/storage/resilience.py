"""Прерыватель обращений к внешним сервисам (circuit breaker).

Тот же, что у сервисов авторизации, уведомлений и Async API: поведение при
отказе соседа одинаковое во всём кинотеатре намеренно.
"""

from collections.abc import Callable
from time import monotonic


class CircuitBreaker:
    """Прерыватель обращений к внешнему сервису (circuit breaker).

    Когда внешний сервис лежит, каждый запрос к нему ждёт таймаута, а вместе с
    ним ждёт и клиент. Прерыватель считает сбои подряд и после порога перестаёт
    пропускать запросы: они сразу получают отказ, и вызывающий код переходит к
    запасному поведению мгновенно, а не через таймаут.

    Состояния: closed (запросы идут) → open (после N сбоев подряд, на
    reset_timeout секунд) → half-open (проходит один пробный запрос: успех
    закрывает прерыватель, сбой открывает его снова).

    Счётчик живёт в процессе: у каждого воркера uvicorn он свой. Общее
    состояние здесь не нужно — порог маленький, и каждый воркер выясняет
    недоступность за считанные запросы. Блокировок тоже нет: корутины одного
    цикла событий не прерывают друг друга между этими операциями.
    """

    def __init__(self, failures: int, reset_timeout: float, clock: Callable[[], float] = monotonic) -> None:
        self._failures_threshold = failures
        self._reset_timeout = reset_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """Открыт ли прерыватель прямо сейчас — обратное к `allows()`, для журнала и тестов."""
        return self._opened_at is not None

    def allows(self) -> bool:
        """Можно ли отправлять запрос: прерыватель закрыт либо пора пробовать снова."""
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
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._failures_threshold:
            # Неудачный пробный запрос из half-open тоже продлевает паузу.
            self._opened_at = self._clock()
