"""Генерация событий для исследования хранилищ.

Десять миллионов событий одним процессом генерируются и грузятся долго, поэтому
работа делится между процессами: каждый берёт свой отрезок и льёт его в
хранилище сам. Генерация идёт **пачками через генератор** — десяти миллионов
словарей в памяти не появляется ни на одном шаге.

Данные не случайные, а правдоподобные: фильмы и жанры распределены неравномерно
(Zipf — несколько хитов и длинный хвост), время событий размазано по году с
вечерним пиком. На равномерно случайных данных любое колоночное хранилище
выглядит одинаково хорошо, а вопрос «какие фильмы смотрят чаще» теряет смысл.
"""

import random
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

# Столько же типов событий, сколько собирает сервис приёма.
EVENT_TYPES = ('click', 'page_view', 'quality_changed', 'video_completed', 'search_filters_applied')
# Досмотры — самый интересный аналитике тип, поэтому их доля заметная.
EVENT_WEIGHTS = (35, 30, 5, 20, 10)
GENRES = (
    'drama', 'comedy', 'action', 'sci-fi', 'documentary',
    'thriller', 'horror', 'animation', 'romance', 'western',
)
PLATFORMS = ('web', 'ios', 'android', 'smart_tv')
PLATFORM_WEIGHTS = (45, 20, 25, 10)

# Каталог кинотеатра и его аудитория: идентификаторы берутся из этих пределов,
# чтобы агрегаты получались осмысленными, а не «по одному событию на фильм».
FILMS = 50_000
USERS = 5_000_000
SESSIONS_PER_USER = 30

YEAR_DAYS = 365


def make_uuid(kind: int, number: int) -> UUID:
    """Собирает UUID из числа: так идентификаторы воспроизводимы.

    Случайный UUID на каждое из десяти миллионов событий стоил бы заметного
    времени, а тесту нужна повторяемость: оба хранилища должны получить
    ровно одни и те же данные.
    """
    return UUID(int=(kind << 96) | number)


def rows(count: int, seed: int, start: datetime | None = None) -> Iterator[Sequence[Any]]:
    """Отдаёт `count` строк событий; `seed` делает набор воспроизводимым."""
    rng = random.Random(seed)  # noqa: S311 — тестовые данные, а не криптография
    start = start or datetime.now(UTC) - timedelta(days=YEAR_DAYS)

    for number in range(count):
        event_type = rng.choices(EVENT_TYPES, weights=EVENT_WEIGHTS, k=1)[0]
        # Zipf по фильмам: у хитов событий на порядки больше, чем у хвоста, —
        # как в жизни, и именно это делает запрос «топ фильмов» осмысленным.
        film_number = min(FILMS, int(rng.paretovariate(1.2)))
        user_number = rng.randrange(USERS)
        occurred_at = start + timedelta(
            days=rng.randrange(YEAR_DAYS),
            # Вечерний пик: события концентрируются к 19–23 часам.
            hours=min(23, int(abs(rng.gauss(20, 3)))),
            minutes=rng.randrange(60),
            seconds=rng.randrange(60),
        )
        yield (
            make_uuid(1, seed * count + number),
            event_type,
            occurred_at,
            make_uuid(2, user_number),
            make_uuid(3, user_number * SESSIONS_PER_USER + rng.randrange(SESSIONS_PER_USER)),
            make_uuid(4, film_number),
            GENRES[film_number % len(GENRES)],
            # Досмотры распределены к единице, но с длинным левым хвостом:
            # часть фильмов бросают в начале — это и ищет аналитика.
            round(min(1.0, max(0.0, rng.betavariate(5, 2))), 3),
            rng.randrange(60_000, 3 * 60 * 60_000),
            rng.choices(PLATFORMS, weights=PLATFORM_WEIGHTS, k=1)[0],
        )


def batches(count: int, seed: int, size: int) -> Iterator[list[Sequence[Any]]]:
    """Режет поток строк на пачки заданного размера.

    Пачка — это то, что уходит в хранилище одним запросом. В ClickHouse
    вставлять по строке нельзя: каждая вставка создаёт на диске новый кусок.
    """
    batch: list[Sequence[Any]] = []
    for row in rows(count, seed):
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
