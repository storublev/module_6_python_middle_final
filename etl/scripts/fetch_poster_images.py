"""Загружает картинки обложек в базу каталога (`content.film_poster`).

Ссылка на обложку у фильма есть с двух сторон: с Кинопоиска
(`film_kinopoisk.poster_url`, её находит `fetch_kinopoisk.py`) и из каталога
(`film_work.poster_url` — IMDb или правка редактора). Кинопоиск в приоритете:
у него обложки русских прокатных изданий. Скрипт скачивает картинку и кладёт
её байты в базу — страницы кинотеатра не зависят от того, жив ли чужой CDN и
пускает ли он к себе. Отдаёт картинки админка по адресу `/posters/<id>.jpg`,
ETL пишет этот адрес в индекс вместо внешней ссылки.

Повторный запуск докачивает только новое: картинка, скачанная с того же
адреса, повторно не скачивается. Правка ссылки редактором в админке удаляет
загруженную картинку, и следующий запуск скачает новую.

    python scripts/fetch_poster_images.py
    python scripts/fetch_poster_images.py --workers 8
"""

import argparse
import logging
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

USER_AGENT = "practix-posters/1.0 (+https://github.com/storublev/module_6_python_middle_final)"
TIMEOUT = 20
# Обложка в карточке — 300–400 точек по ширине; больше двух мегабайт — это
# не обложка, а ошибка источника.
MAX_SIZE = 2 * 1024 * 1024

WANTED = """
SELECT fw.id,
       COALESCE(kp.poster_url, CASE WHEN fw.poster_url LIKE 'http%%' THEN fw.poster_url END) AS source_url,
       CASE WHEN kp.poster_url IS NOT NULL THEN 'kinopoisk' ELSE 'catalog' END AS source,
       fp.source_url AS loaded_url
FROM content.film_work fw
LEFT JOIN content.film_kinopoisk kp ON kp.film_id = fw.id AND kp.found
LEFT JOIN content.film_poster fp ON fp.film_id = fw.id
"""
UPSERT = """
INSERT INTO content.film_poster (film_id, content, content_type, source, source_url, fetched_at)
VALUES (%(film_id)s, %(content)s, %(content_type)s, %(source)s, %(source_url)s, now())
ON CONFLICT (film_id) DO UPDATE SET
    content = EXCLUDED.content, content_type = EXCLUDED.content_type, source = EXCLUDED.source,
    source_url = EXCLUDED.source_url, fetched_at = now()
"""
# Через журнал аудита ETL узнаёт, что у фильма сменился адрес обложки.
TOUCH_FILM = "UPDATE content.film_work SET modified = now() WHERE id = %(film_id)s"

logger = logging.getLogger("fetch_poster_images")


def download_url(url: str) -> str:
    """Адрес, с которого качать: у Кинопоиска — уменьшенная копия, её хватает карточке."""
    return url.replace("/images/posters/kp/", "/images/posters/kp_small/")


def download(url: str, retries: int = 3) -> Optional[tuple[bytes, str]]:
    """Байты картинки и её тип или None, если по адресу не картинка.

    Обрыв соединения повторяется с паузой: CDN и API Кинопоиска иногда рвут
    TLS посреди серии запросов, а через пару секунд отвечают снова.
    """
    request = urllib.request.Request(download_url(url), headers={"User-Agent": USER_AGENT})  # noqa: S310
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 — адрес из каталога
                content_type = response.headers.get_content_type()
                body = response.read(MAX_SIZE + 1)
            break
        except urllib.error.HTTPError as error:
            logger.warning("%s: HTTP %s", url, error.code)
            return None
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == retries:
                logger.warning("%s: %s", url, error)
                return None
            time.sleep(2 ** attempt)
    if not content_type.startswith("image/") or not body or len(body) > MAX_SIZE:
        logger.warning("%s: не картинка (%s, %d байт)", url, content_type, len(body))
        return None
    return body, content_type


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=8, help="сколько картинок качать одновременно")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from db.postgres import get_pg_connection

    conn = get_pg_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(WANTED)
            todo = [
                dict(row) for row in cursor.fetchall()
                if row["source_url"] and row["source_url"] != row["loaded_url"]
            ]
        logger.info("Обложек к загрузке: %d", len(todo))
        loaded = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for film, result in zip(todo, pool.map(lambda film: download(film["source_url"]), todo), strict=True):
                if result is None:
                    continue
                content, content_type = result
                with conn.cursor() as cursor:
                    cursor.execute(UPSERT, {
                        "film_id": str(film["id"]), "content": content, "content_type": content_type,
                        "source": film["source"], "source_url": film["source_url"],
                    })
                    cursor.execute(TOUCH_FILM, {"film_id": str(film["id"])})
                conn.commit()
                loaded += 1
                if loaded % 100 == 0:
                    logger.info("Загружено: %d из %d", loaded, len(todo))
    finally:
        conn.close()
    logger.info("Готово: загружено %d обложек", loaded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
