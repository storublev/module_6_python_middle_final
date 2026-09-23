"""Подключение к PostgreSQL и извлечение данных."""

import logging
import time
import functools
from typing import Any, Generator, Optional

import psycopg2
from psycopg2.extras import DictCursor

from core.config import settings

logger = logging.getLogger(__name__)


def backoff(
    start_delay: float = 1.0,
    max_delay: float = 60.0,
    max_retries: int = 10,
) -> Any:
    """Декоратор для экспоненциального backoff при ошибках подключения."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = start_delay
            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
                    last_exc = exc
                    logger.warning(
                        "[%d/%d] Ошибка соединения: %s. Повтор через %.1f с",
                        attempt, max_retries, exc, delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, max_delay)
            logger.error("Исчерпаны все попытки подключения к PostgreSQL.")
            raise last_exc  # type: ignore
        return wrapper
    return decorator


def get_pg_connection():
    """Создаёт и возвращает соединение с PostgreSQL с backoff."""
    @backoff(max_retries=10)
    def _connect():
        conn = psycopg2.connect(settings.dsn, cursor_factory=DictCursor)
        return conn
    return _connect()


def fetch_movies(
    cursor,
    last_modified: str,
    limit: int,
    offset: int = 0
) -> list[dict[str, Any]]:
    """Выполняет запрос на получение фильмов, изменённых после last_modified.

    Args:
        cursor: Курсор PostgreSQL.
        last_modified: Дата в формате 'YYYY-MM-DD HH:MM:SS.mmmmmm'.
        limit: Максимальное количество записей.
        offset: Смещение для пагинации.

    Returns:
        Список строк-словарей с данными фильмов.
    """
    from core.queries import FETCH_MODIFIED_MOVIES

    # Используем OFFSET для правильной пагинации
    query = FETCH_MODIFIED_MOVIES.format(
        last_modified=last_modified,
        limit=limit,
        offset=offset
    )
    cursor.execute(query)
    return [dict(row) for row in cursor.fetchall()]


def get_max_modified_in_batch(batch: list[dict]) -> Optional[str]:
    """Извлекает максимальную дату изменения из батча."""
    if not batch:
        return None

    max_modified = None
    for row in batch:
        modified = row.get("modified")
        if modified:
            # Приводим к строковому представлению
            if hasattr(modified, "isoformat"):
                modified_str = modified.isoformat()
            else:
                modified_str = str(modified)

            if max_modified is None or modified_str > max_modified:
                max_modified = modified_str

    return max_modified


def iter_movie_batches(
    last_modified: str,
    batch_size: int = 100,
) -> Generator[list[dict[str, Any]], None, Optional[str]]:
    """Итератор по батчам фильмов из PostgreSQL с правильной пагинацией.

    Args:
        last_modified: Стартовая дата.
        batch_size: Размер батча.

    Yields:
        Очередной батч записей.

    Returns:
        Последняя дата modified из последнего батча (или None).
    """
    conn = get_pg_connection()
    try:
        with conn.cursor() as cursor:
            current_modified = last_modified
            offset = 0
            total_processed = 0

            while True:
                # Получаем батч с учетом смещения
                rows = fetch_movies(cursor, current_modified, batch_size, offset)

                if not rows:
                    # Данные закончились
                    logger.info(f"📭 Данных больше нет. Всего обработано: {total_processed}")
                    break

                logger.info(f"📦 Получено {len(rows)} записей (offset={offset})")

                # Проверяем, не зациклились ли мы
                if rows and total_processed > 0:
                    first_modified = rows[0].get("modified")
                    last_modified_in_batch = rows[-1].get("modified")
                    logger.debug(f"📅 Диапазон дат: {first_modified} -> {last_modified_in_batch}")

                yield rows

                # Обновляем счетчики
                total_processed += len(rows)
                offset += len(rows)

                # Обновляем current_modified на основе максимальной даты в батче
                batch_max_modified = get_max_modified_in_batch(rows)
                if batch_max_modified and batch_max_modified > current_modified:
                    current_modified = batch_max_modified
                    logger.debug(f"📝 Обновлена дата: {current_modified}")

                # Если получили меньше записей, чем запросили - данных больше нет
                if len(rows) < batch_size:
                    logger.info(f"✅ Последний батч ({len(rows)} < {batch_size}). Завершаем.")
                    break

                # Небольшая пауза между батчами
                time.sleep(0.05)

    except Exception as e:
        logger.error(f"❌ Ошибка в итераторе: {e}", exc_info=True)
        raise
    finally:
        conn.close()

    # Возвращаем последнюю дату для сохранения состояния
    return current_modified if current_modified != last_modified else None


def count_modified_movies(last_modified: str) -> int:
    """Подсчитывает количество фильмов, изменённых после указанной даты.

    Args:
        last_modified: Дата в формате 'YYYY-MM-DD HH:MM:SS.mmmmmm'.

    Returns:
        Количество фильмов.
    """
    conn = get_pg_connection()
    try:
        with conn.cursor() as cursor:
            from core.queries import COUNT_MODIFIED_MOVIES
            query = COUNT_MODIFIED_MOVIES.format(last_modified=last_modified)
            cursor.execute(query)
            return cursor.fetchone()[0]
    finally:
        conn.close()
