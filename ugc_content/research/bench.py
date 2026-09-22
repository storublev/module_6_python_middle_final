"""Сравнение MongoDB и PostgreSQL на данных пользовательского контента.

Что меряется и почему именно это:

* **чтение на загруженных данных** — сценарии из урока: агрегат по фильму,
  список понравившихся, закладки, рецензии. Требование НФТ-2: агрегат не
  дольше 200 мс, и меряется он **перцентилями**, а не средним: среднее прячет
  как раз те запросы, из-за которых карточка грузится долго;
* **горячие фильмы отдельно** — у хита сотни тысяч оценок, и агрегат по нему
  стоит на порядки дороже, чем по фильму из хвоста. Если мерить вперемешку,
  Парето утопит редкие тяжёлые запросы в массе лёгких;
* **свежесть** — через сколько поставленная оценка видна в агрегате (НФТ-6);
* **чтение под нагрузкой** — то же самое, пока идёт непрерывная запись;
* **вставка и место на диске** — второстепенное: подсказка урока прямо
  говорит, что скорость записи здесь не так важна, как скорость чтения.

Запуск (нужны поднятые хранилища — ugc_content/research/docker-compose.yml):

    python bench.py all --storage mongodb --rows 10000000 --report results-mongodb.json
    python bench.py all --storage postgresql --rows 10000000 --report results-postgresql.json

Каждый шаг можно запустить отдельно: `load`, `read`, `freshness`,
`read-under-load`.
"""

import argparse
import json
import os
import random
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from generate import (
    BOOKMARKS_PER_LIKE,
    FILMS,
    REVIEWS_PER_LIKE,
    USERS,
    batches,
    bookmarks,
    film_uuid,
    hot_films,
    likes,
    reviews,
    user_uuid,
)
from storages import MongoStorage, PostgresStorage, Storage

DEFAULT_ROWS = 10_000_000
DEFAULT_BATCH = 20_000
DEFAULT_WORKERS = 4
# Перцентили считаются по сотням замеров: на трёх повторах p95 не существует.
DEFAULT_REPEAT = 200
PAGE_SIZE = 20
# Сколько верхних фильмов каталога считаем горячими. Их агрегаты — самые
# тяжёлые запросы сервиса.
HOT_FILMS = 20


def thousands(number: float) -> str:
    """Число с неразрывными пробелами между разрядами: 10 000 000 читается, 10000000 — нет."""
    return f'{number:,.0f}'.replace(',', ' ')


@dataclass
class Timing:
    """Замер одного сценария: перцентили в миллисекундах."""

    name: str
    calls: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    note: str = ''


@dataclass
class Report:
    """Результаты прогона одного хранилища."""

    storage: str
    rows: dict[str, int] = field(default_factory=dict)
    size_mb: float = 0.0
    load_seconds: float = 0.0
    load_rows_per_second: float = 0.0
    counters_seconds: float = 0.0
    reads: list[Timing] = field(default_factory=list)
    reads_under_load: list[Timing] = field(default_factory=list)
    freshness_ms: float = 0.0

    def add(self, timing: Timing, under_load: bool = False) -> Timing:
        (self.reads_under_load if under_load else self.reads).append(timing)
        mark = ' (под нагрузкой)' if under_load else ''
        print(
            f'  {timing.name}{mark}: p50 {timing.p50_ms} мс, p95 {timing.p95_ms} мс, '
            f'p99 {timing.p99_ms} мс, максимум {timing.max_ms} мс {timing.note}',
            flush=True,
        )
        return timing


@contextmanager
def measured() -> Iterator[Callable[[], float]]:
    """Отдаёт функцию, возвращающую время блока в секундах."""
    start = time.perf_counter()
    elapsed = 0.0
    yield lambda: elapsed
    elapsed = time.perf_counter() - start


def percentiles(samples: Sequence[float], name: str, note: str = '') -> Timing:
    """Считает перцентили по списку длительностей в секундах."""
    ordered = sorted(samples)

    def at(share: float) -> float:
        index = min(len(ordered) - 1, int(len(ordered) * share))
        return round(ordered[index] * 1000, 2)

    return Timing(
        name=name,
        calls=len(ordered),
        p50_ms=at(0.50),
        p95_ms=at(0.95),
        p99_ms=at(0.99),
        max_ms=round(ordered[-1] * 1000, 2),
        note=note,
    )


def build_storage(kind: str) -> Storage:
    """Собирает хранилище по имени; адреса берутся из окружения."""
    if kind == 'mongodb':
        return MongoStorage(
            uri=os.getenv('RESEARCH_MONGO_URI', 'mongodb://localhost:27018'),
            database=os.getenv('RESEARCH_MONGO_DB', 'research'),
        )
    return PostgresStorage(
        dsn=os.getenv(
            'RESEARCH_POSTGRES_DSN',
            'postgresql://research:research@localhost:5434/research',
        ),
    )


def load_slice(kind: str, count: int, seed: int, batch_size: int) -> int:
    """Грузит свой отрезок данных: оценки, закладки и рецензии в тех же долях.

    Функция верхнего уровня, а не замыкание: её должен уметь забрать
    ProcessPoolExecutor.
    """
    storage = build_storage(kind)
    written = 0
    try:
        for batch in batches(likes(count, seed), batch_size):
            storage.insert_likes(batch)
            written += len(batch)
        for batch in batches(bookmarks(int(count * BOOKMARKS_PER_LIKE), seed), batch_size):
            storage.insert_bookmarks(batch)
            written += len(batch)
        for batch in batches(reviews(max(1, int(count * REVIEWS_PER_LIKE)), seed), batch_size):
            storage.insert_reviews(batch)
            written += len(batch)
    finally:
        storage.close()
    return written


def load(kind: str, report: Report, rows: int, batch_size: int, workers: int) -> None:
    """Наполняет хранилище в несколько процессов."""
    print(f'Загрузка {thousands(rows)} оценок в {workers} процесса…', flush=True)
    per_worker = rows // workers
    with measured() as elapsed:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            written = sum(pool.map(load_slice, [kind] * workers,
                                   [per_worker] * workers,
                                   range(workers),
                                   [batch_size] * workers))
    report.load_seconds = round(elapsed(), 1)
    report.load_rows_per_second = round(written / elapsed(), 0) if elapsed() else 0.0
    print(f'Загружено {thousands(written)} записей за {report.load_seconds} с '
          f'({thousands(report.load_rows_per_second)} записей/с)', flush=True)


def build_counters(storage: Storage, report: Report) -> None:
    """Строит готовые агрегаты по фильмам и засекает, сколько это стоит.

    В бою счётчик двигается на каждую оценку, а разом он пересчитывается
    только при восстановлении после сбоя — вот это время здесь и меряется.
    """
    print('Построение счётчиков по всем фильмам…', flush=True)
    with measured() as elapsed:
        storage.build_counters()
    report.counters_seconds = round(elapsed(), 1)
    print(f'Счётчики построены за {report.counters_seconds} с', flush=True)


def read(storage: Storage, report: Report, repeat: int, under_load: bool = False) -> None:
    """Прогоняет сценарии чтения и складывает перцентили в отчёт."""
    rng = random.Random(20260922)  # noqa: S311 — выбор фильмов для замера, не криптография
    hot = hot_films(HOT_FILMS)

    samples = []
    for _ in range(repeat):
        film = film_uuid(rng.randrange(1, FILMS))
        with measured() as elapsed:
            storage.film_rating(film)
        samples.append(elapsed())
    report.add(percentiles(samples, 'агрегат по случайному фильму'), under_load)

    samples = []
    # Горячих фильмов в замере немного, и повторять по ним тяжёлый агрегат
    # двести раз — это десятки минут впустую: картина видна и на меньшем числе.
    hot_repeat = max(10, repeat // 10)
    for number in range(hot_repeat):
        with measured() as elapsed:
            storage.film_rating(hot[number % len(hot)])
        samples.append(elapsed())
    report.add(percentiles(samples, 'агрегат по горячему фильму', f'топ-{HOT_FILMS} каталога'), under_load)

    samples = []
    for _ in range(repeat):
        film = film_uuid(rng.randrange(1, FILMS))
        with measured() as elapsed:
            storage.film_rating_counter(film)
        samples.append(elapsed())
    report.add(percentiles(samples, 'счётчик по случайному фильму'), under_load)

    samples = []
    for number in range(repeat):
        with measured() as elapsed:
            storage.film_rating_counter(hot[number % len(hot)])
        samples.append(elapsed())
    report.add(percentiles(samples, 'счётчик по горячему фильму', f'топ-{HOT_FILMS} каталога'), under_load)

    samples = []
    for _ in range(repeat):
        user = user_uuid(rng.randrange(USERS))
        with measured() as elapsed:
            storage.liked_films(user, PAGE_SIZE)
        samples.append(elapsed())
    report.add(percentiles(samples, 'понравившиеся фильмы зрителя'), under_load)

    samples = []
    for _ in range(repeat):
        user = user_uuid(rng.randrange(USERS))
        with measured() as elapsed:
            storage.bookmarks(user, PAGE_SIZE)
        samples.append(elapsed())
    report.add(percentiles(samples, 'закладки зрителя'), under_load)

    for sort in ('newest', 'most_useful'):
        samples = []
        for number in range(repeat):
            film = hot[number % len(hot)]
            with measured() as elapsed:
                storage.reviews(film, sort, PAGE_SIZE)
            samples.append(elapsed())
        report.add(percentiles(samples, f'рецензии горячего фильма ({sort})'), under_load)


def freshness(storage: Storage, report: Report, repeat: int) -> None:
    """Меряет, через сколько поставленная оценка видна в агрегате (НФТ-6).

    Меряется не «сколько идёт запись», а сколько проходит от записи до момента,
    когда агрегат её учитывает: для пользователя «лайк не появился» и «сервис
    сломался» — одно и то же.
    """
    print('Свежесть: от записи оценки до её появления в агрегате', flush=True)
    samples = []
    for number in range(repeat):
        # Фильм из хвоста каталога: агрегат по нему дешёвый, и в замер попадает
        # задержка появления, а не время подсчёта сотни тысяч оценок.
        film = film_uuid(FILMS - number - 1)
        user = user_uuid(USERS - number - 1)
        rating = 10 if number % 2 else 0
        start = time.perf_counter()
        storage.set_rating(film, user, rating, datetime.now(UTC))
        while True:
            likes_count, dislikes_count, _ = storage.film_rating(film)
            if likes_count + dislikes_count > 0:
                break
            if time.perf_counter() - start > 5:
                break
        samples.append(time.perf_counter() - start)
    timing = percentiles(samples, 'оценка видна в агрегате')
    report.freshness_ms = timing.p95_ms
    report.add(timing)


def write_until(kind: str, seed: int, batch_size: int, seconds: float) -> int:
    """Пишет в хранилище заданное время — фон для чтения под нагрузкой."""
    storage = build_storage(kind)
    written = 0
    deadline = time.perf_counter() + seconds
    try:
        # Смещённое зерно: фоновые писатели не должны повторять пары
        # «зритель — фильм», уже загруженные основным прогоном.
        for batch in batches(likes(10_000_000, seed), batch_size):
            storage.insert_likes(batch)
            written += len(batch)
            if time.perf_counter() > deadline:
                break
    except Exception as error:  # noqa: BLE001 — фоновая нагрузка не должна ронять замер
        print(f'фоновая запись остановлена: {error}', flush=True)
    finally:
        storage.close()
    return written


def read_under_load(kind: str, storage: Storage, report: Report, repeat: int,
                    batch_size: int, writers: int, seconds: float = 120.0) -> None:
    """Те же чтения, но пока в хранилище идёт непрерывная запись."""
    print(f'Чтение под нагрузкой: {writers} процесса пишут {seconds:.0f} с', flush=True)
    with ProcessPoolExecutor(max_workers=writers) as pool:
        futures = [
            pool.submit(write_until, kind, 100 + number, batch_size, seconds)
            for number in range(writers)
        ]
        read(storage, report, repeat, under_load=True)
        written = sum(future.result() for future in futures)
    print(f'Фоном записано {thousands(written)} оценок', flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Сравнение MongoDB и PostgreSQL')
    parser.add_argument(
        'command',
        choices=['all', 'prepare', 'load', 'counters', 'read', 'freshness', 'read-under-load'],
    )
    parser.add_argument('--storage', choices=['mongodb', 'postgresql'], required=True)
    parser.add_argument('--rows', type=int, default=DEFAULT_ROWS)
    parser.add_argument('--batch', type=int, default=DEFAULT_BATCH)
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS)
    parser.add_argument('--repeat', type=int, default=DEFAULT_REPEAT)
    parser.add_argument('--report', help='куда сохранить результаты в JSON')
    parser.add_argument('--keep', action='store_true', help='не удалять данные перед загрузкой')
    args = parser.parse_args(argv)

    storage = build_storage(args.storage)
    report = Report(storage=storage.name)
    try:
        if args.command in ('all', 'prepare', 'load') and not args.keep:
            storage.drop()
        storage.prepare()

        if args.command in ('all', 'load'):
            load(args.storage, report, args.rows, args.batch, args.workers)
            storage.after_load()
        if args.command in ('all', 'counters'):
            build_counters(storage, report)
        if args.command in ('all', 'read', 'freshness', 'read-under-load'):
            report.rows = storage.counts()
            report.size_mb = storage.size_mb()
            print(f'В хранилище {report.rows}, {thousands(report.size_mb)} МБ', flush=True)
        if args.command in ('all', 'read'):
            read(storage, report, args.repeat)
        if args.command in ('all', 'freshness'):
            freshness(storage, report, min(args.repeat, 50))
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
