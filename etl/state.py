# state.py
"""Хранение состояния ETL-процесса в PostgreSQL."""

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from db.postgres import get_pg_connection, backoff

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """JSON-сериализатор, поддерживающий datetime."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class DatabaseStateStorage:
    """Хранилище состояния в PostgreSQL."""

    def __init__(self):
        """Инициализирует хранилище и создает таблицу при необходимости."""
        self._ensure_table_exists()

    @backoff(max_retries=5)
    def _ensure_table_exists(self) -> None:
        """Создает таблицу состояния, если она не существует."""
        conn = get_pg_connection()
        try:
            with conn.cursor() as cursor:
                # Проверяем существование таблицы
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'content'
                        AND table_name = 'etl_state'
                    )
                """)
                exists = cursor.fetchone()[0]

                if not exists:
                    logger.info("📝 Создание таблицы etl_state...")
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS content.etl_state (
                            key VARCHAR(100) PRIMARY KEY,
                            value TEXT NOT NULL,
                            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS idx_etl_state_key
                        ON content.etl_state(key)
                    """)

                    # Вставляем начальные значения
                    cursor.execute("""
                        INSERT INTO content.etl_state (key, value) VALUES
                            ('last_audit_id', '0'),
                            ('last_modified', '1970-01-01 00:00:00.000000'),
                            ('statistics', '{"total_processed": 0, "total_runs": 0}')
                        ON CONFLICT (key) DO NOTHING
                    """)

                    conn.commit()
                    logger.info("✅ Таблица etl_state создана")
        except Exception as e:
            logger.error(f"❌ Ошибка при создании таблицы состояния: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()

    @backoff(max_retries=5)
    def get(self, key: str, default: Any = None) -> Any:
        """Получает значение по ключу."""
        conn = get_pg_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT value FROM content.etl_state WHERE key = %s",
                    (key,)
                )
                result = cursor.fetchone()

                if not result:
                    return default

                value = result[0]
                # Пытаемся распарсить JSON
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return value
        finally:
            conn.close()

    @backoff(max_retries=5)
    def set(self, key: str, value: Any) -> None:
        """Устанавливает значение по ключу."""
        # Преобразуем в JSON если это словарь или список
        if isinstance(value, (dict, list)):
            value = json.dumps(value, cls=DateTimeEncoder)
        else:
            value = str(value)

        conn = get_pg_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO content.etl_state (key, value, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_at = CURRENT_TIMESTAMP
                """, (key, value))
                conn.commit()
                logger.debug(f"💾 Сохранен ключ: {key}={value[:100]}...")
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении ключа {key}: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()


class State:
    """Обёртка над хранилищем для удобного чтения/записи ключей с кэшированием."""

    def __init__(self, storage: Optional[DatabaseStateStorage] = None):
        self._storage = storage or DatabaseStateStorage()
        self._cache = {}
        self._cache_time = {}
        self._cache_ttl = 5  # Кэшируем на 5 секунд

    def get(self, key: str, default: Any = None) -> Any:
        """Возвращает значение по ключу или default."""
        # Проверяем кэш
        if key in self._cache:
            if time.time() - self._cache_time.get(key, 0) < self._cache_ttl:
                return self._cache[key]

        value = self._storage.get(key, default)
        self._cache[key] = value
        self._cache_time[key] = time.time()
        return value

    def set(self, key: str, value: Any) -> None:
        """Устанавливает значение по ключу и сохраняет."""
        self._storage.set(key, value)
        self._cache[key] = value
        self._cache_time[key] = time.time()

    def get_last_audit_id(self) -> int:
        """Возвращает последний обработанный audit_id."""
        return int(self.get('last_audit_id', 0))

    def set_last_audit_id(self, audit_id: int) -> None:
        """Устанавливает последний обработанный audit_id."""
        self.set('last_audit_id', str(audit_id))

    def get_last_modified(self) -> str:
        """Возвращает последнюю дату изменения."""
        return self.get('last_modified', '1970-01-01 00:00:00.000000')

    def set_last_modified(self, modified: str) -> None:
        """Устанавливает последнюю дату изменения."""
        self.set('last_modified', modified)

    def get_statistics(self) -> Dict[str, Any]:
        """Получает статистику ETL."""
        return self.get('statistics', {})

    def update_statistics(self, updates: Dict[str, Any]) -> None:
        """Обновляет статистику."""
        stats = self.get_statistics()
        stats.update(updates)
        stats['last_updated'] = datetime.now().isoformat()
        self.set('statistics', stats)

    def increment_counter(self, counter_name: str, amount: int = 1) -> int:
        """Инкрементирует счетчик в статистике."""
        stats = self.get_statistics()
        current = stats.get(counter_name, 0)
        new_value = current + amount
        stats[counter_name] = new_value
        stats['last_updated'] = datetime.now().isoformat()
        self.set('statistics', stats)
        return new_value
