"""Скачивание обложки сразу в базу каталога (`content.film_poster`).

Общее для скриптов поиска обложек (IMDb — `fetch_posters.py`, Кинопоиск —
`fetch_kinopoisk.py`). Адрес картинки нужен только на время скачивания: в
базе остаются сами байты и тип, а откуда они взяты, не хранится (ADR-25).
"""

import logging
import time
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

USER_AGENT = "practix-posters/1.0 (+https://github.com/storublev/module_6_python_middle_final)"
TIMEOUT = 20
# Обложка в карточке — 300–400 точек по ширине; больше двух мегабайт — это
# не обложка, а ошибка источника.
MAX_SIZE = 2 * 1024 * 1024

UPSERT = """
INSERT INTO content.film_poster (film_id, content, content_type, fetched_at)
VALUES (%(film_id)s, %(content)s, %(content_type)s, now())
ON CONFLICT (film_id) DO UPDATE SET
    content = EXCLUDED.content, content_type = EXCLUDED.content_type, fetched_at = now()
"""
INSERT_IF_ABSENT = """
INSERT INTO content.film_poster (film_id, content, content_type, fetched_at)
VALUES (%(film_id)s, %(content)s, %(content_type)s, now())
ON CONFLICT (film_id) DO NOTHING
"""
# Через журнал аудита ETL узнаёт, что у фильма сменилась обложка.
TOUCH_FILM = "UPDATE content.film_work SET modified = now() WHERE id = %(film_id)s"


def download(url: str, retries: int = 3) -> Optional[tuple[bytes, str]]:
    """Байты картинки и её тип или None, если по адресу не картинка.

    Обрыв соединения повторяется с паузой: CDN иногда рвут TLS посреди серии
    запросов, а через пару секунд отвечают снова.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 — адрес от поиска
                content_type = response.headers.get_content_type()
                body = response.read(MAX_SIZE + 1)
            break
        except urllib.error.HTTPError as error:
            logger.warning("Картинка не скачана: HTTP %s", error.code)
            return None
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == retries:
                logger.warning("Картинка не скачана: %s", error)
                return None
            time.sleep(2 ** attempt)
    if not content_type.startswith("image/") or not body or len(body) > MAX_SIZE:
        logger.warning("По адресу не картинка (%s, %d байт)", content_type, len(body))
        return None
    return body, content_type


def store_poster(conn, film_id: str, url: str, replace: bool) -> bool:  # noqa: ANN001 — соединение psycopg2
    """Скачивает картинку и кладёт в базу. `replace` — заменить уже загруженную.

    Returns:
        True, если картинка сохранена.
    """
    image = download(url)
    if image is None:
        return False
    content, content_type = image
    params = {"film_id": film_id, "content": content, "content_type": content_type}
    with conn.cursor() as cursor:
        cursor.execute(UPSERT if replace else INSERT_IF_ABSENT, params)
        stored = cursor.rowcount > 0
        if stored:
            cursor.execute(TOUCH_FILM, {"film_id": film_id})
    conn.commit()
    return stored
