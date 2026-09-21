"""Мониторинг памяти приложения.

ETL работает непрерывно: он не перезапускается «сам собой», и утечка в нём
проявляется не падением, а медленным ростом потребления — через неделю
контейнер убивает OOM killer. Отсюда требование НФТ-12: за сутки работы рост
RSS не больше 5%.

Что сделано, чтобы память не росла:

* события обрабатываются пачками и нигде не накапливаются: после вставки
  пачка становится мусором, глобальных списков и кешей в ETL нет;
* преобразование событий в строки идёт генератором — второй копии пачки в
  памяти не появляется;
* счётчики — числа, а не растущие списки.

Сам сторож ничего не чинит: он замеряет RSS и пишет его в журнал, а при
переходе предела предупреждает. Перезапускать контейнер — дело оркестратора,
а не приложения: упасть посреди пачки хуже, чем занять лишние сто мегабайт.
"""

import logging
import os
from dataclasses import dataclass, field

import psutil

logger = logging.getLogger(__name__)

BYTES_IN_MB = 1024 * 1024


def rss_mb(process: psutil.Process | None = None) -> float:
    """Сколько памяти занимает процесс прямо сейчас, в мегабайтах.

    Именно текущее потребление, а не пиковое: по пику (`ru_maxrss`) не видно,
    что память вернулась, — а для поиска утечки важно как раз это.
    """
    return (process or psutil.Process()).memory_info().rss / BYTES_IN_MB


@dataclass
class MemoryWatch:
    """Следит за потреблением памяти и пишет о нём в журнал.

    Запоминает потребление на старте: интересен не сам объём (он зависит от
    размера пачки), а его рост относительно начала работы.
    """

    report_every: int
    limit_mb: float
    process: psutil.Process = field(default_factory=psutil.Process)
    baseline_mb: float = 0.0
    batches: int = 0

    def __post_init__(self) -> None:
        self.baseline_mb = rss_mb(self.process)
        logger.info('Память на старте: %.1f МБ, предел %.0f МБ', self.baseline_mb, self.limit_mb)

    def batch_done(self, rows: int) -> None:
        """Отмечает обработанную пачку и раз в `report_every` пачек замеряет память."""
        self.batches += 1
        if self.batches % self.report_every:
            return

        current = rss_mb(self.process)
        logger.info(
            'Обработано пачек: %d, последняя — %d строк; память %.1f МБ (%+.1f МБ к старту)',
            self.batches, rows, current, current - self.baseline_mb,
        )
        if current > self.limit_mb:
            logger.warning(
                'Память превысила предел: %.1f МБ при пределе %.0f МБ (процесс %d)',
                current, self.limit_mb, os.getpid(),
            )
