#!/usr/bin/env python
"""ETL-процесс с использованием Change Data Capture (CDC).

Основной функционал:
1. Отслеживание изменений в PostgreSQL через таблицу аудита
2. Синхронизация данных с Elasticsearch: индексы movies, genres и persons
3. Обработка INSERT, UPDATE, DELETE операций
4. Хранение состояния в PostgreSQL
5. Автоматическое восстановление после сбоев
"""

import logging
import sys
import time
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from access import subscription_threshold
from core.config import settings
from core.queries import (
    FETCH_FILM_IDS_BY_GENRES,
    FETCH_FILM_IDS_BY_PERSONS,
    FETCH_FILM_IDS_LEAVING_SUBSCRIPTION,
    FETCH_IDS_PAGE,
)
from core.schemas import GENRES_INDEX, MOVIES_INDEX, PERSONS_INDEX
from db.elastic import ensure_index, get_es_client, sync_documents
from db.postgres import backoff, get_pg_connection
from pipelines import MOVIES_PIPELINE, PIPELINES, Pipeline
from state import DatabaseStateStorage, State

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, getattr(settings, 'log_level', 'INFO'), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# Константы
STATE_KEY_AUDIT = "last_audit_id"
# Прогресс начальной загрузки индекса: последний загруженный ID или LOAD_DONE
STATE_KEY_INITIAL_LOAD = "initial_load:{index}"
LOAD_DONE = "done"
# Дата, на которую метки доступа фильмов в индексе актуальны
STATE_KEY_ACCESS_LEVELS = "access_levels_date"
MIN_UUID = "00000000-0000-0000-0000-000000000000"


def _changed(change: Dict[str, Any], field: str) -> bool:
    """Изменилось ли поле записи в UPDATE."""
    old_data = change.get("old_data") or {}
    new_data = change.get("new_data") or {}
    return old_data.get(field) != new_data.get(field)


def _values(change: Dict[str, Any], field: str) -> Set[str]:
    """Значения поля до и после изменения.

    Связь могла переехать с одной записи на другую, поэтому обновлять
    нужно обе стороны.
    """
    return {
        str(data[field])
        for data in (change.get("old_data"), change.get("new_data"))
        if data and data.get(field)
    }


def collect_affected_ids(
    changes: List[Dict[str, Any]],
) -> Tuple[Dict[str, Set[str]], Set[str], Set[str]]:
    """Определяет по записям аудита, какие документы каких индексов обновить.

    Args:
        changes: Записи из content.audit_log

    Returns:
        ID документов по индексам, а также ID переименованных жанров и
        персон: их фильмы тоже нужно переиндексировать, в документе фильма
        хранятся имена.
    """
    affected: Dict[str, Set[str]] = {pipeline.index: set() for pipeline in PIPELINES}
    renamed_genres: Set[str] = set()
    renamed_persons: Set[str] = set()

    for change in changes:
        table = change["table_name"]
        record_id = str(change["record_id"])
        is_update = change["operation"] == "U"

        if table == "film_work":
            affected[MOVIES_INDEX].add(record_id)
        elif table == "genre":
            affected[GENRES_INDEX].add(record_id)
            if is_update and _changed(change, "name"):
                renamed_genres.add(record_id)
        elif table == "person":
            affected[PERSONS_INDEX].add(record_id)
            if is_update and _changed(change, "full_name"):
                renamed_persons.add(record_id)
        elif table == "genre_film_work":
            affected[MOVIES_INDEX] |= _values(change, "film_work_id")
            affected[GENRES_INDEX] |= _values(change, "genre_id")
        elif table == "person_film_work":
            affected[MOVIES_INDEX] |= _values(change, "film_work_id")
            affected[PERSONS_INDEX] |= _values(change, "person_id")

    return affected, renamed_genres, renamed_persons


class AuditETL:
    """ETL с отслеживанием изменений через таблицу аудита."""

    def __init__(self):
        """Инициализация ETL сервиса."""
        # Инициализируем состояние в БД
        self.storage = DatabaseStateStorage()
        self.state = State(self.storage)

        # Последний обработанный ID в таблице аудита
        self.last_audit_id = int(self.state.get(STATE_KEY_AUDIT, 0))

        # Счетчики для статистики
        self.stats = {
            "total_processed": 0,
            "inserts": 0,
            "updates": 0,
            "deletes": 0,
            "errors": 0,
            "start_time": time.time(),
            "last_processed": None,
        }

        # Загружаем сохраненную статистику
        saved_stats = self.state.get_statistics()
        if saved_stats:
            logger.info(f"📊 Загружена статистика: {saved_stats.get('total_processed', 0)} всего обработано")

        # Подключаемся к Elasticsearch
        logger.info("🔌 Подключение к Elasticsearch...")
        self.es = get_es_client()
        for pipeline in PIPELINES:
            if ensure_index(self.es, pipeline.index, pipeline.index_body):
                # В схеме новые поля: документы загружаются заново, с начала.
                self.state.set(STATE_KEY_INITIAL_LOAD.format(index=pipeline.index), MIN_UUID)
        logger.info("✅ Elasticsearch готов")

        logger.info(f"📌 ETL инициализирован. Последний audit_id: {self.last_audit_id}")

    @backoff(max_retries=5)
    def get_unprocessed_changes(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Получает необработанные изменения из аудит-лога.

        Args:
            limit: Максимальное количество записей за раз

        Returns:
            Список изменений
        """
        conn = get_pg_connection()
        try:
            with conn.cursor(cursor_factory=conn.cursor_factory) as cursor:
                cursor.execute("""
                    SELECT
                        id,
                        table_name,
                        record_id,
                        operation,
                        changed_at,
                        old_data,
                        new_data
                    FROM content.audit_log
                    WHERE id > %s
                      AND processed = FALSE
                    ORDER BY id ASC
                    LIMIT %s
                """, (self.last_audit_id, limit))

                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"❌ Ошибка при получении изменений: {e}")
            raise
        finally:
            conn.close()

    @backoff(max_retries=5)
    def mark_changes_processed(self, change_ids: List[int]) -> None:
        """Отмечает изменения как обработанные.

        Args:
            change_ids: Список ID изменений для отметки
        """
        if not change_ids:
            return

        conn = get_pg_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE content.audit_log
                    SET processed = TRUE,
                        processed_at = CURRENT_TIMESTAMP
                    WHERE id = ANY(%s)
                """, (change_ids,))
                conn.commit()
                logger.debug(f"✅ Отмечено {len(change_ids)} изменений как обработанные")
        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении статуса: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()

    @backoff(max_retries=5)
    def fetch_rows(self, query: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Выполняет запрос к PostgreSQL и возвращает строки-словари.

        Args:
            query: SQL-запрос с именованными параметрами
            params: Значения параметров

        Returns:
            Строки результата
        """
        conn = get_pg_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def fetch_ids(self, query: str, ids: Iterable[str]) -> Set[str]:
        """Выполняет запрос, возвращающий колонку id, для списка ID-параметров."""
        ids = list(ids)
        if not ids:
            return set()
        return {str(row["id"]) for row in self.fetch_rows(query, {"ids": ids})}

    def sync(self, pipeline: Pipeline, ids: Iterable[str]) -> int:
        """Синхронизирует документы индекса с PostgreSQL пачками по batch_size.

        Записи, найденные в БД, индексируются, а отсутствующие удаляются из
        индекса — так одинаково обрабатываются вставка, изменение и удаление.

        Args:
            pipeline: Пайплайн сущности
            ids: ID записей для синхронизации

        Returns:
            Количество выполненных в Elasticsearch операций
        """
        ids = sorted(set(ids))
        total = 0
        for start in range(0, len(ids), settings.batch_size):
            chunk = ids[start:start + settings.batch_size]
            rows = self.fetch_rows(pipeline.query, {"ids": chunk})
            documents = {str(row["id"]): pipeline.transform(row) for row in rows}
            total += sync_documents(self.es, pipeline.index, documents, chunk)
        return total

    def process_changes(self, changes: List[Dict[str, Any]]) -> int:
        """Переносит в Elasticsearch изменения из аудит-лога.

        Изменения отмечаются обработанными, только если все индексы успешно
        обновлены; иначе исключение уходит в цикл run() и пачка повторится.
        Повторная обработка безопасна: документы берутся из текущего
        состояния БД.
        """
        if not changes:
            return 0

        logger.info(f"📦 Обработка {len(changes)} изменений...")

        affected, renamed_genres, renamed_persons = collect_affected_ids(changes)
        affected[MOVIES_INDEX] |= self.fetch_ids(FETCH_FILM_IDS_BY_GENRES, renamed_genres)
        affected[MOVIES_INDEX] |= self.fetch_ids(FETCH_FILM_IDS_BY_PERSONS, renamed_persons)

        for pipeline in PIPELINES:
            ids = affected[pipeline.index]
            if ids:
                logger.info(f"🔄 «{pipeline.index}»: синхронизация {len(ids)} документов")
                self.sync(pipeline, ids)

        change_ids = [change['id'] for change in changes]
        self.mark_changes_processed(change_ids)
        self.last_audit_id = max(change_ids)
        self.state.set(STATE_KEY_AUDIT, str(self.last_audit_id))

        operations = {'I': 'inserts', 'U': 'updates', 'D': 'deletes'}
        for change in changes:
            counter = operations.get(change['operation'])
            if counter:
                self.stats[counter] += 1
        self.stats['total_processed'] += len(change_ids)

        self.state.increment_counter('total_processed', len(change_ids))
        self.state.increment_counter('total_runs', 1)
        self.state.update_statistics({
            'last_run': datetime.now().isoformat(),
            'last_audit_id': self.last_audit_id,
            'inserts': self.stats['inserts'],
            'updates': self.stats['updates'],
            'deletes': self.stats['deletes'],
            'errors': self.stats['errors'],
        })

        logger.info(f"💾 Состояние сохранено в БД: audit_id={self.last_audit_id}")
        return len(change_ids)

    def count_documents(self, index: str) -> int:
        """Возвращает количество документов в индексе."""
        count = self.es.count(index=index).get('count', 0)
        logger.info(f"📊 В индексе «{index}» найдено {count} документов")
        return count

    def initial_load(self, pipeline: Pipeline, last_id: str) -> int:
        """Загружает все записи таблицы пайплайна в индекс.

        Записи перебираются по возрастанию ID, после каждой пачки ID
        сохраняется в состоянии — после перезапуска загрузка продолжится
        с места остановки.

        Args:
            pipeline: Пайплайн сущности
            last_id: ID, после которого продолжить загрузку

        Returns:
            Количество выполненных в Elasticsearch операций
        """
        logger.info(f"📦 Начальная загрузка «{pipeline.index}» после ID {last_id}...")
        state_key = STATE_KEY_INITIAL_LOAD.format(index=pipeline.index)
        query = FETCH_IDS_PAGE.format(table=pipeline.table)
        total = 0

        while True:
            rows = self.fetch_rows(query, {"last_id": last_id, "limit": settings.batch_size})
            if not rows:
                break

            ids = [str(row["id"]) for row in rows]
            total += self.sync(pipeline, ids)
            last_id = ids[-1]
            self.state.set(state_key, last_id)

        self.state.set(state_key, LOAD_DONE)
        logger.info(f"✅ Начальная загрузка «{pipeline.index}» завершена: {total} операций")
        return total

    def ensure_elasticsearch_data(self):
        """Выполняет начальную загрузку индексов, которые ещё не загружены.

        Пустой индекс загружается с начала, незавершённая загрузка
        продолжается с сохранённого ID.
        """
        for pipeline in PIPELINES:
            state_key = STATE_KEY_INITIAL_LOAD.format(index=pipeline.index)
            progress = self.state.get(state_key)

            if self.count_documents(pipeline.index) == 0:
                progress = MIN_UUID
            elif progress == LOAD_DONE:
                logger.info(f"✅ Индекс «{pipeline.index}» уже загружен")
                continue

            self.initial_load(pipeline, last_id=str(progress or MIN_UUID))

    def refresh_access_levels(self, today: Optional[date] = None) -> int:
        """Раз в сутки снимает метку «по подписке» с фильмов, которые перестали быть новинками.

        Метка вычисляется при индексации фильма и сама не меняется, а фильм
        выходит из новинок просто с течением времени. Поэтому за каждый день
        с прошлой проверки переиндексируются фильмы, у которых срок новинки
        истёк в этот промежуток. Первый запуск только запоминает дату:
        начальная загрузка уже расставила метки на сегодня.

        Returns:
            Количество переиндексированных фильмов
        """
        today = today or date.today()
        checked = self.state.get(STATE_KEY_ACCESS_LEVELS)
        if checked == today.isoformat():
            return 0

        ids: List[str] = []
        if checked:
            rows = self.fetch_rows(FETCH_FILM_IDS_LEAVING_SUBSCRIPTION, {
                "since": subscription_threshold(date.fromisoformat(checked)),
                "until": subscription_threshold(today),
            })
            ids = [str(row["id"]) for row in rows]
        if ids:
            logger.info(f"🔄 Фильмов, которые больше не новинки: {len(ids)}")
            self.sync(MOVIES_PIPELINE, ids)
        self.state.set(STATE_KEY_ACCESS_LEVELS, today.isoformat())
        return len(ids)

    def print_statistics(self):
        """Выводит статистику работы ETL."""
        stats = self.state.get_statistics()
        runtime = time.time() - self.stats['start_time']

        logger.info("=" * 60)
        logger.info("📊 СТАТИСТИКА ETL")
        logger.info(f"   Всего обработано: {stats.get('total_processed', 0)}")
        logger.info(f"   Запусков: {stats.get('total_runs', 0)}")
        logger.info(f"   INSERT: {self.stats['inserts']}")
        logger.info(f"   UPDATE: {self.stats['updates']}")
        logger.info(f"   DELETE: {self.stats['deletes']}")
        logger.info(f"   Ошибок: {self.stats['errors']}")
        logger.info(f"   Время работы: {runtime:.2f}с")
        logger.info(f"   Последний audit_id: {self.last_audit_id}")
        if stats.get('last_run'):
            logger.info(f"   Последний запуск: {stats['last_run']}")
        logger.info("=" * 60)

    def run_once(self) -> int:
        """Выполняет один цикл ETL.

        Returns:
            Количество обработанных изменений
        """
        self.refresh_access_levels()

        # Получаем новые изменения
        changes = self.get_unprocessed_changes()

        if not changes:
            return 0

        logger.info(f"🔍 Найдено {len(changes)} необработанных изменений")
        processed = self.process_changes(changes)
        logger.info(f"✅ Обработано {processed} изменений")
        return processed

    def run(self):
        """Запускает ETL в бесконечном цикле."""
        logger.info("🚀 Запуск ETL с Change Data Capture...")
        logger.info(f"📌 Последний audit_id: {self.last_audit_id}")

        # Проверяем и загружаем данные в Elasticsearch при первом старте
        self.ensure_elasticsearch_data()

        empty_cycles = 0
        poll_interval = getattr(settings, 'poll_interval', 10)
        sleep_time = getattr(settings, 'sleep_time', 1)

        while True:
            try:
                processed = self.run_once()

                if processed > 0:
                    empty_cycles = 0
                    time.sleep(sleep_time)
                else:
                    empty_cycles += 1

                    if empty_cycles > 10:
                        wait_time = min(60, empty_cycles * 2)
                        logger.debug(f"⏳ Долгое ожидание: {wait_time}с (пустых циклов: {empty_cycles})")
                        time.sleep(wait_time)
                    else:
                        time.sleep(poll_interval)

                if empty_cycles % 10 == 0 and empty_cycles > 0:
                    self.print_statistics()

            except KeyboardInterrupt:
                logger.info("🛑 ETL остановлен пользователем")
                break
            except Exception as e:
                self.stats['errors'] += 1
                logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
                logger.info(f"⏳ Повторная попытка через {poll_interval}с...")
                time.sleep(poll_interval)


def main():
    """Точка входа."""
    try:
        etl = AuditETL()
        etl.run()
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
