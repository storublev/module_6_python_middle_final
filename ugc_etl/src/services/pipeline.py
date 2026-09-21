"""Перенос событий: читаем пачку, вставляем, подтверждаем.

Слой бизнес-логики. Не знает ни про Kafka, ни про ClickHouse — только про
интерфейсы источника и приёмника, поэтому проверяется без обоих.

Порядок действий здесь и есть главная гарантия ETL: смещения подтверждаются
**после** успешной вставки. Падение между вставкой и подтверждением приведёт
к повтору пачки, а не к её потере (at-least-once); повторы убирает
ReplacingMergeTree в ClickHouse.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import sleep

from core.memory import MemoryWatch
from models.event import to_rows
from storage.base import (
    EventSink,
    EventSource,
    SinkDataError,
    SinkUnavailableError,
    SourceUnavailableError,
)
from storage.resilience import CircuitBreaker, retry_with_backoff

logger = logging.getLogger(__name__)


@dataclass
class RetryPolicy:
    """Сколько раз и с какой паузой повторять вставку."""

    retries: int
    base: float
    cap: float


@dataclass
class Stats:
    """Счётчики работы. Именно счётчики, а не списки: ETL работает неделями."""

    batches: int = 0
    rows: int = 0
    # Событие не дошло до строки: не разобралось или нарушило контракт.
    skipped: int = 0
    # Строка дошла, но хранилище её не приняло: значение не лезет в колонку.
    rejected: int = 0


class EventPipeline:
    """Непрерывный перенос событий из источника в аналитическое хранилище."""

    def __init__(
        self,
        source: EventSource,
        sink: EventSink,
        memory: MemoryWatch,
        policy: RetryPolicy,
        breaker: CircuitBreaker,
        breaker_pause: float,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self._source = source
        self._sink = sink
        self._memory = memory
        self._policy = policy
        self._breaker = breaker
        self._breaker_pause = breaker_pause
        self._sleeper = sleeper
        self._stopped = False
        self.stats = Stats()

    def stop(self) -> None:
        """Просит перенос завершиться: текущая пачка дойдёт до конца."""
        self._stopped = True

    def run(self) -> None:
        """Читает пачки, пока не попросят остановиться.

        Raises:
            SourceUnavailableError: источник перестал отвечать.
        """
        logger.info('Перенос событий запущен')
        for batch in self._source.batches():
            if self._stopped:
                break
            if not batch:
                # Пустая пачка — это не сбой, а ночное затишье: новых событий
                # за отведённое время не пришло.
                continue
            self._process(batch)
        logger.info(
            'Перенос остановлен: пачек %d, строк %d, пропущено событий %d, отброшено строк %d',
            self.stats.batches, self.stats.rows, self.stats.skipped, self.stats.rejected,
        )

    def _process(self, batch: Sequence[dict]) -> None:
        """Превращает пачку в строки, вставляет и подтверждает смещения."""
        # Генератор превращается в список здесь и только здесь: драйверу нужна
        # длина пачки, а второй копии событий в памяти не появляется.
        rows = list(to_rows(batch))
        self.stats.skipped += len(batch) - len(rows)

        if rows and not self._deliver(rows):
            # Остановились, не вставив пачку: подтверждать нечего — эти
            # события прочитает следующий запуск.
            return

        self._commit()
        self.stats.batches += 1
        self.stats.rows += len(rows)
        self._memory.batch_done(len(rows))

    def _deliver(self, rows: Sequence[Sequence]) -> bool:
        """Вставляет пачку, пока не получится или пока не попросят остановиться.

        Бросать пачку нельзя: смещения ещё не подтверждены, но потребитель уже
        сдвинулся, и молча пропущенные события вернулись бы только после
        перезапуска. Kafka хранит события неделю — столько ETL и может ждать
        хранилище.

        Недоступность и негодные данные разведены намеренно. Первую можно
        пережидать вечно, вторую — нельзя: пачка с числом, которое не влезает
        в колонку, не станет пригодной, сколько её ни повторяй, и остановила бы
        перенос навсегда.
        """
        while not self._stopped:
            if not self._breaker.allows():
                logger.warning('Прерыватель открыт: ждём %.0f с и пробуем снова', self._breaker_pause)
                self._sleeper(self._breaker_pause)
                continue
            try:
                self._insert(rows)
            except SinkUnavailableError as error:
                logger.error('Хранилище недоступно после всех повторов: %s', error)
                self._breaker.record_failure()
                continue
            except SinkDataError as error:
                # Хранилище живо — оно отказало по существу, поэтому
                # прерыватель не трогаем, а ищем, какие именно строки негодны.
                logger.error('Хранилище не приняло данные пачки, ищем негодные строки: %s', error)
                self._breaker.record_success()
                if self._insert_without_broken(rows):
                    return True
                continue
            self._breaker.record_success()
            return True
        return False

    def _insert_without_broken(self, rows: Sequence[Sequence]) -> bool:
        """Делит пачку пополам, пока не найдёт строки, которые хранилище не принимает.

        Отбросить всю пачку из-за одной строки было бы расточительно: в ней
        десять тысяч событий. Деление пополам находит негодные за десяток
        вставок вместо десяти тысяч.

        Возвращает False, если посреди разбора отвалилось само хранилище: тогда
        вызывающий начнёт с целой пачки заново. Часть строк при этом уже
        вставлена и повторится — по этой же причине таблица собрана на
        ReplacingMergeTree.
        """
        if len(rows) == 1:
            logger.error('Строка отброшена: хранилище не принимает её значения (событие %s)', rows[0][0])
            self.stats.rejected += 1
            return True

        middle = len(rows) // 2
        for half in (rows[:middle], rows[middle:]):
            try:
                self._insert(half)
            except SinkDataError:
                if not self._insert_without_broken(half):
                    return False
            except SinkUnavailableError as error:
                logger.error('Хранилище отвалилось во время разбора пачки: %s', error)
                self._breaker.record_failure()
                return False
        return True

    def _insert(self, rows: Sequence[Sequence]) -> None:
        """Вставка с повторами: короткий сбой хранилища стоит переждать."""
        retry_with_backoff(
            lambda: self._sink.insert(rows),
            retries=self._policy.retries,
            base=self._policy.base,
            cap=self._policy.cap,
            errors=(SinkUnavailableError,),
            sleeper=self._sleeper,
        )

    def _commit(self) -> None:
        """Подтверждает смещения; сбой источника здесь не теряет данные.

        Если подтвердить не удалось, пачка придёт снова после перезапуска или
        перераспределения партиций — то есть повторится, а не пропадёт.
        """
        try:
            self._source.commit()
        except SourceUnavailableError as error:
            logger.warning('Не удалось подтвердить смещения, пачка повторится: %s', error)
