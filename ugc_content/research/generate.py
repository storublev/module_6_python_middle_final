"""Генерация пользовательского контента для исследования хранилищ.

Десять миллионов оценок одним процессом грузятся долго, поэтому работа делится
между процессами: каждый берёт свой отрезок и льёт его в хранилище сам.
Генерация идёт **пачками через генератор** — десяти миллионов словарей в
памяти не появляется ни на одном шаге.

Данные не равномерно случайные, а правдоподобные, иначе исследование потеряло
бы смысл. Фильмы распределены по Парето: у хитов сотни тысяч оценок, у хвоста
единицы. Именно на горячих фильмах и проверяется бюджет в 200 мс — на
равномерном шуме любое хранилище выглядит одинаково хорошо, потому что каждый
агрегат считается по десятку строк.

Идентификаторы собираются из чисел, а не берутся случайными: так оба хранилища
получают **ровно один и тот же набор данных**, и сравнение честное.
"""

import random
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

# Каталог и аудитория кинотеатра — те же числа, что в требованиях.
FILMS = 50_000
USERS = 5_000_000
# Исследование наполняет базы объёмом примерно трёх недель работы кинотеатра,
# поэтому и период, по которому размазано время, — три недели.
PERIOD_DAYS = 21

# Доли данных друг относительно друга взяты из метрик нагрузки: на 500 000
# оценок в сутки приходится 100 000 закладок и 1 000 рецензий.
BOOKMARKS_PER_LIKE = 0.2
REVIEWS_PER_LIKE = 0.002

# Оценки смещены к верху шкалы: зрители чаще досматривают и хвалят то, что
# сами выбрали. Дизлайки редки, но их достаточно, чтобы агрегат не выродился.
RATING_WEIGHTS = (3, 1, 1, 2, 3, 5, 8, 14, 20, 25, 18)

REVIEW_WORDS = (
    'сюжет', 'актёры', 'режиссёр', 'финал', 'музыка', 'съёмка', 'сценарий',
    'атмосфера', 'герой', 'диалоги', 'темп', 'юмор', 'драма', 'развязка',
)


def make_uuid(kind: int, number: int) -> UUID:
    """Собирает UUID из числа: так идентификаторы воспроизводимы.

    Случайный UUID на каждую из десяти миллионов записей стоил бы заметного
    времени, а исследованию нужна повторяемость: оба хранилища должны получить
    одни и те же пары «зритель — фильм».
    """
    return UUID(int=(kind << 96) | number)


def film_uuid(number: int) -> UUID:
    """Идентификатор фильма по его номеру в каталоге."""
    return make_uuid(4, number)


def user_uuid(number: int) -> UUID:
    """Идентификатор зрителя по его номеру."""
    return make_uuid(2, number)


def hot_films(count: int) -> list[UUID]:
    """Самые популярные фильмы каталога — у них больше всего оценок.

    Парето выдаёт маленькие числа чаще больших, поэтому горячие фильмы — это
    фильмы с малыми номерами. Замеры обязаны трогать именно их: агрегат по
    фильму с тремя оценками быстрый в любом хранилище.
    """
    return [film_uuid(number) for number in range(1, count + 1)]


# Множитель Кнута для дешёвого детерминированного хеша: по номеру зрителя он
# даёт равномерное число, а из него — тот же фильм при каждом запуске.
HASH_MULTIPLIER = 2_654_435_761


def _film_from_uniform(uniform: float, repeat: int = 0) -> int:
    """Номер фильма из равномерного числа: логарифмически равномерно по каталогу.

    Распределение выбрано с плотностью ~1/x (`FILMS ** u`), а не Парето. Парето
    с длинным хвостом на таком каталоге вырождается: один фильм забирает больше
    трети всех оценок, чего в жизни не бывает. Здесь же на самый популярный
    фильм приходится около 6% оценок, на первую двадцатку — около 28%, а хвост
    из десятков тысяч фильмов получает единицы. Это и есть та картина, ради
    которой ставился эксперимент: агрегат по хиту считается по сотням тысяч
    записей, по фильму из хвоста — по десятку.

    `repeat` сдвигает номер: повторная запись того же зрителя достаётся
    другому фильму, поэтому пара «зритель — фильм» уникальна по построению.
    """
    draw = min(FILMS, max(1, int(FILMS ** uniform)))
    return ((draw - 1 + repeat) % FILMS) + 1


def _film_number(rng: random.Random) -> int:
    """Номер фильма для записей, где уникальность пары не нужна (рецензии)."""
    return _film_from_uniform(rng.random())


def _film_for(user_number: int, repeat: int, salt: int = 0) -> int:
    """Фильм для пары «зритель — фильм», уникальной по построению.

    Случайный выбор фильма здесь не годится: при десяти миллионах записей и
    пяти миллионах зрителей один и тот же зритель попадается дважды, и
    случайность рано или поздно выдаёт ему один и тот же фильм — уникальный
    индекс такую запись отклоняет, и прогон падает на середине. Поэтому фильм
    выводится из номера зрителя детерминированно (хеш Кнута), а повторные
    записи сдвигаются на `repeat`.
    """
    hashed = ((user_number + salt) * HASH_MULTIPLIER) % 2 ** 32
    return _film_from_uniform(hashed / 2 ** 32, repeat)


def _moment(rng: random.Random, start: datetime) -> datetime:
    """Время действия: равномерно по периоду, но с вечерним пиком внутри суток."""
    return start + timedelta(
        days=rng.randrange(PERIOD_DAYS),
        hours=min(23, int(abs(rng.gauss(20, 3)))),
        minutes=rng.randrange(60),
        seconds=rng.randrange(60),
        microseconds=rng.randrange(1_000_000),
    )


def likes(count: int, seed: int, start: datetime | None = None) -> Iterator[Sequence[Any]]:
    """Отдаёт `count` оценок: (film_id, user_id, rating, created_at).

    Пара «зритель — фильм» уникальна: зритель оценивает фильм один раз.
    Уникальность обеспечивается построением (см. `_film_for`), а не удачей:
    при десяти миллионах записей случайный выбор фильма давал бы совпадения
    сотнями, и прогон падал бы на уникальном индексе в середине загрузки.
    """
    rng = random.Random(seed)  # noqa: S311 — тестовые данные, а не криптография
    start = start or datetime.now(UTC) - timedelta(days=PERIOD_DAYS)

    for number in range(count):
        global_number = seed * count + number
        user_number = global_number % USERS
        # Сколько раз этот зритель уже встречался: по нему сдвигается фильм,
        # поэтому вторая оценка того же зрителя достаётся другому фильму.
        repeat = global_number // USERS
        yield (
            film_uuid(_film_for(user_number, repeat)),
            user_uuid(user_number),
            rng.choices(range(11), weights=RATING_WEIGHTS, k=1)[0],
            _moment(rng, start),
        )


def bookmarks(count: int, seed: int, start: datetime | None = None) -> Iterator[Sequence[Any]]:
    """Отдаёт `count` закладок: (user_id, film_id, created_at)."""
    rng = random.Random(seed + 1_000_000)  # noqa: S311
    start = start or datetime.now(UTC) - timedelta(days=PERIOD_DAYS)

    for number in range(count):
        global_number = seed * count + number
        user_number = global_number % USERS
        # Соль отличает закладки от оценок: иначе зритель откладывал бы ровно
        # те фильмы, которые оценил, и выборки стали бы подозрительно похожи.
        yield (
            user_uuid(user_number),
            film_uuid(_film_for(user_number, global_number // USERS, salt=7)),
            _moment(rng, start),
        )


def reviews(count: int, seed: int, start: datetime | None = None) -> Iterator[Sequence[Any]]:
    """Отдаёт `count` рецензий: (review_id, film_id, user_id, text, rating, useful, useless, created_at).

    Голоса за полезность тоже по Парето: у большинства рецензий их единицы, у
    немногих — тысячи. Без этого сортировка «самые полезные» ничего не значила
    бы: все рецензии оказались бы равны.
    """
    rng = random.Random(seed + 2_000_000)  # noqa: S311
    start = start or datetime.now(UTC) - timedelta(days=PERIOD_DAYS)

    for number in range(count):
        review_number = seed * count + number
        user_number = review_number % USERS
        useful = min(5_000, int(rng.paretovariate(1.3)) - 1)
        text = ' '.join(rng.choices(REVIEW_WORDS, k=rng.randrange(60, 240)))
        yield (
            make_uuid(5, review_number),
            film_uuid(_film_number(rng)),
            user_uuid(user_number),
            text,
            rng.choices(range(11), weights=RATING_WEIGHTS, k=1)[0],
            useful,
            rng.randrange(0, max(1, useful // 3 + 1)),
            _moment(rng, start),
        )


def batches(source: Iterator[Sequence[Any]], size: int) -> Iterator[list[Sequence[Any]]]:
    """Режет поток записей на пачки заданного размера.

    Пачка — это то, что уходит в хранилище одним запросом: вставлять по одной
    записи десять миллионов раз означало бы мерить не хранилище, а задержку
    сети.
    """
    batch: list[Sequence[Any]] = []
    for row in source:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
