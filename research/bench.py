"""Сравнение ClickHouse и Vertica на данных сервиса сбора событий.

Что меряется и почему именно это:

* **вставка** — сколько строк в секунду хранилище принимает пачками; у нас в
  сутки 60 000 000 событий, и если хранилище не успевает, выбор закончен;
* **чтение** — время аналитических запросов на загруженных данных; требование
  НФТ-7: агрегирующий запрос не дольше 10 секунд;
* **чтение под нагрузкой** — те же запросы, пока в хранилище идёт непрерывная
  запись. Это и есть продакшен: аналитик приходит не в тишину, а в поток
  событий, и именно здесь хранилища расходятся сильнее всего;
* **место на диске** — во что превращаются 10 000 000 событий после сжатия.

Запуск (нужны поднятые хранилища — research/docker-compose.yml):

    python bench.py all --storage clickhouse --rows 10000000
    python bench.py all --storage vertica --rows 10000000 --report results-vertica.json

Каждый шаг можно запустить отдельно: `load`, `read`, `read-under-load`.
"""

import argparse
import json
import multiprocessing
import os
import statistics
import sys
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

from generate import batches
from storages import QUERIES, ClickHouseStorage, Storage, VerticaStorage

DEFAULT_ROWS = 10_000_000
DEFAULT_BATCH = 50_000
DEFAULT_WORKERS = 4
DEFAULT_REPEAT = 3


def thousands(number: float) -> str:
    """Число с неразрывными пробелами между разрядами: 10 000 000 читается, 10000000 — нет."""
    return f'{number:,.0f}'.replace(',', ' ')


@dataclass
class Measurement:
    """Один замер: что мерили, сколько заняло и сколько получилось."""

    name: str
    seconds: float
    rows: int = 0
    note: str = ''

    @property
    def rows_per_second(self) -> float:
        return self.rows / self.seconds if self.seconds else 0.0


@dataclass
class Report:
    """Итог прогона по одному хранилищу."""

    storage: str
    rows: int = 0
    size_mb: float = 0.0
    measurements: list[Measurement] = field(default_factory=list)

    def add(self, measurement: Measurement) -> Measurement:
        self.measurements.append(measurement)
        print(f'  {measurement.name}: {measurement.seconds:.2f} с {measurement.note}'.rstrip(), flush=True)
        return measurement


@contextmanager
def measured() -> Iterator[Callable[[], float]]:
    """Замеряет время блока по монотонным часам."""
    started = time.monotonic()
    elapsed = 0.0
    yield lambda: elapsed or time.monotonic() - started
    elapsed = time.monotonic() - started


def build_storage(kind: str) -> Storage:
    """Создаёт хранилище по имени; адреса и пароли — из окружения."""
    if kind == 'clickhouse':
        return ClickHouseStorage(
            host=os.getenv('CLICKHOUSE_HOST', '127.0.0.1'),
            port=int(os.getenv('CLICKHOUSE_PORT', '8123')),
            user=os.getenv('CLICKHOUSE_USER', 'default'),
            password=os.getenv('CLICKHOUSE_PASSWORD', ''),
            database=os.getenv('CLICKHOUSE_DATABASE', 'research'),
        )
    return VerticaStorage(
        host=os.getenv('VERTICA_HOST', '127.0.0.1'),
        port=int(os.getenv('VERTICA_PORT', '5433')),
        user=os.getenv('VERTICA_USER', 'dbadmin'),
        password=os.getenv('VERTICA_PASSWORD', ''),
        database=os.getenv('VERTICA_SCHEMA', 'research'),
    )


def load_slice(kind: str, count: int, seed: int, batch_size: int) -> int:
    """Работа одного процесса загрузки: сгенерировать свой отрезок и залить его.

    Соединение своё у каждого процесса: делить его между процессами нельзя, да
    и параллельная вставка — часть замера, одним потоком хранилище не нагрузить.
    """
    storage = build_storage(kind)
    written = 0
    try:
        for batch in batches(count, seed=seed, size=batch_size):
            storage.insert(batch)
            written += len(batch)
    finally:
        storage.close()
    return written


def load(kind: str, report: Report, rows: int, batch_size: int, workers: int) -> None:
    """Загружает данные в несколько процессов и замеряет скорость вставки."""
    print(
        f'Загрузка {thousands(rows)} строк в {kind}: {workers} процессов, пачка {thousands(batch_size)}',
        flush=True,
    )
    per_worker = rows // workers
    with measured() as elapsed, ProcessPoolExecutor(max_workers=workers) as pool:
        written = sum(
            future.result() for future in [
                pool.submit(load_slice, kind, per_worker, seed, batch_size) for seed in range(workers)
            ]
        )
    measurement = Measurement('вставка', elapsed(), rows=written)
    measurement.note = f'{thousands(measurement.rows_per_second)} строк/с'
    report.add(measurement)
    report.rows = written


def read(storage: Storage, report: Report, repeat: int, suffix: str = '') -> None:
    """Прогоняет каждый запрос несколько раз и берёт медиану.

    Медиана, а не лучшее время: первый запрос греет кеш страниц, и брать его
    значило бы мерить диск, а последний — уже полностью прогретое хранилище.
    """
    for name in QUERIES:
        sql = storage.query(name)
        times = []
        for _ in range(repeat):
            with measured() as elapsed:
                storage.execute(sql)
            times.append(elapsed())
        report.add(Measurement(
            f'{name}{suffix}',
            statistics.median(times),
            note=f'min {min(times):.2f} с, max {max(times):.2f} с',
        ))


def read_under_load(kind: str, storage: Storage, report: Report, repeat: int, batch_size: int, writers: int) -> None:
    """Те же запросы, пока в хранилище непрерывно пишут.

    Именно так хранилище работает в продакшене: события идут круглые сутки, и
    аналитик приходит не в тишину. Пишущие процессы поднимаются на время
    замера и гасятся сразу после него — общим флагом, а не по таймеру: сколько
    займут запросы, заранее неизвестно.
    """
    print(f'Чтение под нагрузкой: {writers} пишущих процессов', flush=True)
    context = multiprocessing.get_context('spawn')
    stop = context.Event()
    written = context.Queue()
    processes = [
        context.Process(target=write_until, args=(kind, seed + 100, batch_size, stop, written))
        for seed in range(writers)
    ]
    for process in processes:
        process.start()
    try:
        read(storage, report, repeat, suffix=' (под нагрузкой)')
    finally:
        stop.set()
        for process in processes:
            process.join(timeout=60)

    total = sum(written.get() for _ in processes if not written.empty())
    report.add(Measurement('дописано во время чтения', 0, rows=total, note=f'{thousands(total)} строк'))


def write_until(kind: str, seed: int, batch_size: int, stop, written) -> None:
    """Пишет пачки, пока не выставят флаг остановки."""
    storage = build_storage(kind)
    rows = 0
    try:
        for batch in batches(count=10_000_000, seed=seed, size=batch_size):
            if stop.is_set():
                break
            storage.insert(batch)
            rows += len(batch)
    finally:
        storage.close()
        written.put(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Сравнение ClickHouse и Vertica')
    parser.add_argument('command', choices=['all', 'prepare', 'load', 'read', 'read-under-load'])
    parser.add_argument('--storage', choices=['clickhouse', 'vertica'], required=True)
    parser.add_argument('--rows', type=int, default=DEFAULT_ROWS)
    parser.add_argument('--batch', type=int, default=DEFAULT_BATCH)
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS)
    parser.add_argument('--repeat', type=int, default=DEFAULT_REPEAT)
    parser.add_argument('--report', help='куда сохранить результаты в JSON')
    parser.add_argument('--keep', action='store_true', help='не удалять таблицу перед загрузкой')
    args = parser.parse_args(argv)

    storage = build_storage(args.storage)
    report = Report(storage=storage.name)
    try:
        if args.command in ('all', 'prepare', 'load') and not args.keep:
            storage.drop()
        storage.prepare()

        if args.command in ('all', 'load'):
            load(args.storage, report, args.rows, args.batch, args.workers)
        if args.command in ('all', 'read'):
            report.rows = storage.count()
            report.size_mb = storage.size_mb()
            print(f'В хранилище {thousands(report.rows)} строк, {thousands(report.size_mb)} МБ', flush=True)
            read(storage, report, args.repeat)
        if args.command in ('all', 'read-under-load'):
            read_under_load(args.storage, storage, report, args.repeat, args.batch, writers=2)
    finally:
        storage.close()

    if args.report:
        with open(args.report, 'w', encoding='utf-8') as file:
            json.dump(asdict(report), file, ensure_ascii=False, indent=2)
        print(f'Результаты сохранены в {args.report}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
