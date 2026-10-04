"""Подбирает обложки фильмам каталога через поиск IMDb и кладёт картинки в базу.

Картинка скачивается сразу в `content.film_poster` (scripts/poster_store.py);
адрес, по которому её нашли, не сохраняется (ADR-25). Фильмы, у которых
обложка уже есть (загружена редактором или с Кинопоиска), не трогаются.
Скрипт запускается разово — стенд поднимается из дампа базы уже с обложками.

Как ищется. В дампе нет идентификаторов IMDb, поэтому поиск — по названию
через сервис подсказок, которым пользуется строка поиска самого IMDb. Берётся
только **точное** совпадение нормализованного названия: похожий фильм с чужой
обложкой хуже, чем заглушка. По той же причине пропускаются названия, под
которыми в каталоге несколько фильмов: года в каталоге нет, и различить их
нельзя. Идентификатор IMDb найденного фильма записывается в `imdb_id`.

Запуск (нужен доступ к базе каталога, переменные DB_* — как у ETL):

    python scripts/fetch_posters.py
    python scripts/fetch_posters.py --limit 20
"""

import argparse
import json
import logging
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SUGGEST_URL = "https://v2.sg.media-imdb.com/suggestion/{first}/{query}.json"
# Сервис подсказок отвечает и без особых заголовков, но вежливо представиться.
from scripts.poster_store import USER_AGENT, store_poster  # noqa: E402
TIMEOUT = 15
# Пауза между запросами: источник чужой, и долбить его без паузы невежливо.
PAUSE = 0.25
RETRIES = 3

# Какие виды записей IMDb подходят типу фильма каталога. В каталоге под
# «movie» лежат и короткометражки, и видео, и даже игры по фильмам.
KINDS = {
    "movie": {"movie", "tvMovie", "short", "video", "videoGame", "tvSpecial", "musicVideo"},
    "tv_show": {"tvSeries", "tvMiniSeries", "tvSpecial", "tvEpisode", "podcastSeries"},
}
# Размер картинки: CDN IMDb масштабирует её по суффиксу в адресе. 400 точек по
# ширине хватает и на сетку каталога, и на карточку, а весит в разы меньше
# оригинала.
SIZE_SUFFIX = "._V1_QL75_UX400_.jpg"

FILMS_QUERY = """
SELECT fw.id, fw.title, fw.type, fp.film_id IS NOT NULL AS has_poster
FROM content.film_work fw
LEFT JOIN content.film_poster fp ON fp.film_id = fw.id
ORDER BY fw.title, fw.id
"""
SET_IMDB_ID = "UPDATE content.film_work SET imdb_id = %(imdb_id)s WHERE id = %(film_id)s AND imdb_id IS NULL"

logger = logging.getLogger("fetch_posters")


def normalize(title: str) -> str:
    """Название без регистра, диакритики, пунктуации и лишних пробелов."""
    decomposed = unicodedata.normalize("NFKD", title)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    plain = plain.lower().replace("&", " and ")
    return " ".join(re.sub(r"[^\w]+", " ", plain).split())


def resized(image_url: str) -> str:
    """Адрес той же картинки в размере для карточки."""
    return re.sub(r"\._V1_.*\.jpg$", SIZE_SUFFIX, image_url) if "._V1_" in image_url else image_url


def best_match(title: str, film_type: str, candidates: Iterable[dict]) -> Optional[dict]:
    """Выбирает запись IMDb, которая точно соответствует фильму каталога.

    Returns:
        Запись с полями id и i (картинка) или None, если точного совпадения нет.
    """
    wanted = normalize(title)
    exact = [
        item for item in candidates
        if item.get("id", "").startswith("tt") and normalize(item.get("l", "")) == wanted
    ]
    if not exact:
        return None
    kinds = KINDS.get(film_type, set())

    def preference(item: dict) -> tuple[int, int]:
        same_kind = item.get("qid") in kinds
        has_image = bool((item.get("i") or {}).get("imageUrl"))
        return (0 if same_kind else 1, 0 if has_image else 1)

    # sorted устойчив: при равенстве сохраняется порядок IMDb (по популярности).
    return sorted(exact, key=preference)[0]


def ambiguous_titles(films: Iterable[dict]) -> set[str]:
    """Названия, под которыми в каталоге несколько фильмов.

    Года выхода в каталоге нет, и «My Lucky Star» 1938 и 2013 годов по
    названию не различить: какой бы фильм ни выбрал поиск, одному из них
    достанется чужая обложка. Таким фильмам обложку ставит редактор в админке.
    """
    seen: set[str] = set()
    repeated: set[str] = set()
    for film in films:
        title = normalize(film["title"])
        (repeated if title in seen else seen).add(title)
    return repeated


def suggest(title: str) -> list[dict]:
    """Ответ сервиса подсказок IMDb на название."""
    query = normalize(title)
    if not query:
        return []
    url = SUGGEST_URL.format(first=query[0], query=urllib.parse.quote(query))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 — адрес задан константой
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
                return json.load(response).get("d", [])
        except urllib.error.HTTPError as error:
            # 404 — у IMDb нет подсказок на такой запрос, это ответ, а не сбой.
            if error.code == 404:
                return []
            logger.warning("%s: HTTP %s, попытка %d", title, error.code, attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            logger.warning("%s: %s, попытка %d", title, error, attempt)
        time.sleep(PAUSE * 2 ** attempt)
    return []


def load_films(conn, limit: Optional[int]) -> list[dict]:  # noqa: ANN001 — соединение psycopg2
    """Фильмы каталога: id, название, тип и есть ли уже обложка."""
    with conn.cursor() as cursor:
        cursor.execute(FILMS_QUERY + (" LIMIT %s" if limit else ""), (limit,) if limit else None)
        return [dict(row) for row in cursor.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="взять только первые N фильмов")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from db.postgres import get_pg_connection

    conn = get_pg_connection()
    try:
        films = load_films(conn, args.limit)
        ambiguous = ambiguous_titles(films)
        queue = [f for f in films if not f["has_poster"] and normalize(f["title"]) not in ambiguous]
        stored = 0
        for number, film in enumerate(queue, start=1):
            match = best_match(film["title"], film["type"], suggest(film["title"]))
            image = ((match or {}).get("i") or {}).get("imageUrl")
            if match and image:
                with conn.cursor() as cursor:
                    cursor.execute(SET_IMDB_ID, {"imdb_id": match["id"], "film_id": str(film["id"])})
                conn.commit()
                stored += store_poster(conn, str(film["id"]), resized(image), replace=False)
            if number % 50 == 0:
                logger.info("Обработано %d из %d, обложек загружено %d", number, len(queue), stored)
            time.sleep(PAUSE)
    finally:
        conn.close()
    logger.info("Готово: загружено обложек %d из %d фильмов без обложки", stored, len(queue))
    return 0


if __name__ == "__main__":
    sys.exit(main())
