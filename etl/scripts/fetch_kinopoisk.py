"""Дополняет каталог данными Кинопоиска: русское название и описание, рейтинг, год, обложка.

Источник — неофициальный API Кинопоиска (kinopoiskapiunofficial.tech), ключ —
в переменной `KINOPOISK_API_KEY`. Один поиск по названию отдаёт всё нужное,
поэтому на фильм уходит ровно один запрос. Результат пишется прямо в базу
каталога, в таблицу `content.film_kinopoisk` (её заводит
`sql/catalog_extensions.sql`): разобранные поля и полный ответ в `raw`.

**Суточный лимит бесплатного ключа — 500 запросов**, а фильмов в каталоге
почти тысяча. Поэтому скрипт:

* идёт по важности: сначала фильмы без обложки, затем без описания, затем
  остальные по рейтингу — первый же день закрывает дыры в карточках;
* записывает и ненайденные фильмы (`found = false`) и при следующем запуске
  берёт только тех, кого в таблице ещё нет;
* останавливается, как только API ответил, что лимит исчерпан (402 или 429).

Сопоставление — то же правило, что у обложек IMDb (`fetch_posters.py`):
только **точное** совпадение английского или оригинального названия, тип
фильма должен совпасть, а названия, под которыми в каталоге несколько фильмов
или на Кинопоиске несколько записей одного типа, пропускаются — чужое описание
хуже никакого.

Сами картинки обложек загружает следующий шаг — `fetch_poster_images.py`.

    KINOPOISK_API_KEY=... python scripts/fetch_kinopoisk.py
    KINOPOISK_API_KEY=... python scripts/fetch_kinopoisk.py --limit 20
"""

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fetch_posters import ambiguous_titles, normalize  # noqa: E402

SEARCH_URL = "https://kinopoiskapiunofficial.tech/api/v2.1/films/search-by-keyword?{query}"
TIMEOUT = 15
# Лимит API — 20 запросов в секунду; пауза держит нас далеко от него.
PAUSE = 0.2

# Какие типы Кинопоиска подходят типу фильма каталога.
KINDS = {
    "movie": {"FILM", "VIDEO"},
    "tv_show": {"TV_SERIES", "MINI_SERIES", "TV_SHOW"},
}

FILMS_QUERY = """
SELECT fw.id, fw.title, fw.type
FROM content.film_work fw
LEFT JOIN content.film_kinopoisk kp ON kp.film_id = fw.id
WHERE kp.film_id IS NULL
ORDER BY fw.poster_url IS NULL DESC, COALESCE(fw.description, '') = '' DESC, fw.rating DESC NULLS LAST, fw.id
"""
ALL_TITLES_QUERY = "SELECT title FROM content.film_work"

UPSERT = """
INSERT INTO content.film_kinopoisk (
    film_id, found, kinopoisk_id, title_ru, description_ru, year, rating, rating_votes, length,
    countries, genres, poster_url, raw, fetched_at
) VALUES (
    %(film_id)s, %(found)s, %(kinopoisk_id)s, %(title_ru)s, %(description_ru)s, %(year)s, %(rating)s,
    %(rating_votes)s, %(length)s, %(countries)s, %(genres)s, %(poster_url)s, %(raw)s, now()
)
ON CONFLICT (film_id) DO UPDATE SET
    found = EXCLUDED.found, kinopoisk_id = EXCLUDED.kinopoisk_id, title_ru = EXCLUDED.title_ru,
    description_ru = EXCLUDED.description_ru, year = EXCLUDED.year, rating = EXCLUDED.rating,
    rating_votes = EXCLUDED.rating_votes, length = EXCLUDED.length, countries = EXCLUDED.countries,
    genres = EXCLUDED.genres, poster_url = EXCLUDED.poster_url, raw = EXCLUDED.raw, fetched_at = now()
"""
# Изменение строки фильма попадает в журнал аудита — так ETL узнаёт, что
# документ фильма надо переиндексировать с русским названием и описанием.
TOUCH_FILM = "UPDATE content.film_work SET modified = now() WHERE id = %(film_id)s"

logger = logging.getLogger("fetch_kinopoisk")


class QuotaExceeded(Exception):  # noqa: N818 — сигнал остановки, а не ошибка
    """Суточный лимит ключа исчерпан."""


def best_match(title: str, film_type: str, candidates: Iterable[dict]) -> Optional[dict]:
    """Запись Кинопоиска, которая точно соответствует фильму каталога, или None."""
    wanted = normalize(title)
    kinds = KINDS.get(film_type, set())
    exact = [
        item for item in candidates
        if wanted in {normalize(item.get("nameEn") or ""), normalize(item.get("nameOriginal") or "")}
        and item.get("type") in kinds
    ]
    # Несколько точных совпадений одного типа — разные фильмы с одним
    # названием (ремейки), и без года не выбрать. Пропускаем.
    return exact[0] if len(exact) == 1 else None


def clean(text: Optional[str]) -> Optional[str]:
    """Текст одной строкой: без неразрывных пробелов и переводов строк."""
    cleaned = " ".join((text or "").replace("\xa0", " ").split())
    return cleaned or None


def number(value: Any, kind: type) -> Any:
    """Число из ответа Кинопоиска: там бывают «null», «7.8» и «78%»."""
    try:
        return kind(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None


def poster(item: dict) -> Optional[str]:
    """Обложка из записи Кинопоиска. Его заглушка «нет постера» — не обложка."""
    url = item.get("posterUrlPreview") or item.get("posterUrl") or ""
    return None if not url or "no-poster" in url else url


def record(film_id: str, match: Optional[dict]) -> dict:
    """Строка таблицы film_kinopoisk из записи поиска."""
    if match is None:
        empty = dict.fromkeys(
            ("kinopoisk_id", "title_ru", "description_ru", "year", "rating", "rating_votes", "length",
             "countries", "genres", "poster_url", "raw"),
        )
        return {"film_id": film_id, "found": False, **empty}
    return {
        "film_id": film_id,
        "found": True,
        "kinopoisk_id": number(match.get("filmId"), int),
        "title_ru": clean(match.get("nameRu")),
        "description_ru": clean(match.get("description")),
        "year": number(match.get("year"), int),
        "rating": number(match.get("rating"), float),
        "rating_votes": number(match.get("ratingVoteCount"), int),
        "length": match.get("filmLength") or None,
        "countries": [c["country"] for c in match.get("countries") or [] if c.get("country")],
        "genres": [g["genre"] for g in match.get("genres") or [] if g.get("genre")],
        "poster_url": poster(match),
        "raw": json.dumps(match, ensure_ascii=False),
    }


def search(title: str, api_key: str, retries: int = 3) -> list[dict]:
    """Поиск по названию с повтором при обрыве соединения.

    Сервер Кинопоиска иногда рвёт TLS-соединение посреди серии запросов
    (`UNEXPECTED_EOF`); через пару секунд он снова отвечает. Отказ по лимиту и
    ответ 4xx не повторяются — они не пройдут и со второй попытки.
    """
    url = SEARCH_URL.format(query=urllib.parse.urlencode({"keyword": title, "page": 1}))
    request = urllib.request.Request(url, headers={"X-API-KEY": api_key, "Accept": "application/json"})  # noqa: S310
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 — адрес задан константой
                return json.load(response).get("films", [])
        except urllib.error.HTTPError as error:
            if error.code in (402, 429):
                raise QuotaExceeded(f"HTTP {error.code}") from error
            if error.code == 404:
                return []
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=480, help="запросов за запуск: суточный лимит ключа — 500")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api_key = os.environ.get("KINOPOISK_API_KEY")
    if not api_key:
        raise SystemExit("Задайте KINOPOISK_API_KEY")

    from db.postgres import get_pg_connection

    conn = get_pg_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(ALL_TITLES_QUERY)
            ambiguous = ambiguous_titles(dict(row) for row in cursor.fetchall())
            cursor.execute(FILMS_QUERY)
            queue = [dict(row) for row in cursor.fetchall() if normalize(row["title"]) not in ambiguous]
        logger.info("В очереди: %d фильмов (неоднозначные названия пропускаются)", len(queue))

        found = requests = 0
        for film in queue[:args.limit]:
            try:
                match = best_match(film["title"], film["type"], search(film["title"], api_key))
            except QuotaExceeded as error:
                logger.warning("Лимит ключа исчерпан (%s) — продолжим завтра", error)
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                logger.warning("%s: %s — пропущен до следующего запуска", film["title"], error)
                continue
            requests += 1
            found += match is not None
            with conn.cursor() as cursor:
                cursor.execute(UPSERT, record(str(film["id"]), match))
                if match is not None:
                    cursor.execute(TOUCH_FILM, {"film_id": str(film["id"])})
            # Фиксация после каждого фильма: прерванный запуск не теряет
            # сделанного, а запросы из суточного лимита не тратятся повторно.
            conn.commit()
            if requests % 50 == 0:
                logger.info("Запросов: %d, найдено: %d", requests, found)
            time.sleep(PAUSE)
    finally:
        conn.close()
    logger.info("Готово: запросов %d, найдено на Кинопоиске %d", requests, found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
