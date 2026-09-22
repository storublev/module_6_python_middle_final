"""Два хранилища за одним интерфейсом: ClickHouse и Vertica.

Сравнивать их можно только на одинаковых условиях: одна и та же схема, одни и
те же данные, одни и те же запросы, один и тот же размер пачки при вставке.
Поэтому различия спрятаны здесь, а сам замер (bench.py) об этих различиях не
знает и потому не может нечаянно дать одному из хранилищ фору.

Чем схемы всё-таки отличаются и почему:

* ClickHouse — MergeTree с ключом сортировки; в нём нет «проекций сегментов»,
  зато есть партиционирование по месяцу и разреженный индекс по ключу;
* Vertica — колоночное хранение с проекцией; порядок сортировки задаётся в
  проекции (ORDER BY), сегментирование — по хешу, как рекомендует сама Vertica.

Типы подобраны так, чтобы данные хранились одинаково: строки с малым числом
значений — LowCardinality в ClickHouse и VARCHAR в Vertica (у неё словарное
сжатие выбирается автоматически при загрузке).
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

CLICKHOUSE_SCHEMA = """
CREATE DATABASE IF NOT EXISTS {database};

CREATE TABLE IF NOT EXISTS {database}.events
(
    event_id      UUID,
    event_type    LowCardinality(String),
    occurred_at   DateTime64(3, 'UTC'),
    user_id       UUID,
    session_id    UUID,
    film_id       UUID,
    genre         LowCardinality(String),
    watched_ratio Float32,
    duration_ms   UInt32,
    platform      LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(occurred_at)
ORDER BY (event_type, occurred_at, film_id);
"""

VERTICA_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS {database};

CREATE TABLE IF NOT EXISTS {database}.events
(
    event_id      UUID,
    event_type    VARCHAR(32),
    occurred_at   TIMESTAMP,
    user_id       UUID,
    session_id    UUID,
    film_id       UUID,
    genre         VARCHAR(32),
    watched_ratio FLOAT,
    duration_ms   INT,
    platform      VARCHAR(16)
)
ORDER BY event_type, occurred_at, film_id
SEGMENTED BY HASH(event_id) ALL NODES;
"""

COLUMNS = (
    'event_id', 'event_type', 'occurred_at', 'user_id', 'session_id',
    'film_id', 'genre', 'watched_ratio', 'duration_ms', 'platform',
)

# Одни и те же вопросы к обоим хранилищам. Это те вопросы, ради которых
# аналитическое хранилище и заводится (ФТ-9), плюс один тяжёлый запрос без
# фильтра по времени — чтобы увидеть, как хранилище ведёт себя на полном объёме.
QUERIES = {
    'top_films': """
        SELECT film_id, count(*) AS views
        FROM {table}
        WHERE event_type = 'video_completed'
        GROUP BY film_id
        ORDER BY views DESC
        LIMIT 20
    """,
    'abandoned_films': """
        SELECT film_id, avg(watched_ratio) AS finished, count(*) AS views
        FROM {table}
        WHERE event_type = 'video_completed'
        GROUP BY film_id
        HAVING count(*) > 100
        ORDER BY finished ASC
        LIMIT 20
    """,
    'genre_popularity_last_month': """
        SELECT genre, count(*) AS views, count(DISTINCT user_id) AS viewers
        FROM {table}
        WHERE event_type = 'video_completed' AND occurred_at >= {month_ago}
        GROUP BY genre
        ORDER BY views DESC
    """,
    'events_by_platform_and_day': """
        SELECT platform, {day} AS day, count(*) AS events
        FROM {table}
        GROUP BY platform, day
        ORDER BY day DESC, events DESC
        LIMIT 50
    """,
    'full_scan_unique_users': """
        SELECT count(DISTINCT user_id) AS viewers
        FROM {table}
    """,
}


class Storage(ABC):
    """Хранилище, участвующее в сравнении."""

    name: str
    # То, что у хранилищ пишется по-разному и подставляется в общие запросы:
    # полное имя таблицы, выражение «месяц назад» и приведение к дате.
    table: str
    month_ago: str
    day_expression: str

    @abstractmethod
    def prepare(self) -> None:
        """Создаёт схему."""

    @abstractmethod
    def drop(self) -> None:
        """Удаляет таблицу: замер начинается с пустого хранилища."""

    @abstractmethod
    def insert(self, rows: Sequence[Sequence[Any]]) -> None:
        """Вставляет пачку строк."""

    @abstractmethod
    def execute(self, sql: str) -> int:
        """Выполняет запрос и возвращает число полученных строк."""

    @abstractmethod
    def count(self) -> int:
        """Сколько строк в таблице."""

    @abstractmethod
    def size_mb(self) -> float:
        """Сколько места занимают данные на диске, в мегабайтах."""

    @abstractmethod
    def close(self) -> None:
        """Закрывает соединение."""

    def query(self, name: str) -> str:
        """Подставляет в запрос то, что у хранилищ пишется по-разному."""
        return QUERIES[name].format(table=self.table, month_ago=self.month_ago, day=self.day_expression)


class ClickHouseStorage(Storage):
    """ClickHouse через HTTP-интерфейс."""

    name = 'ClickHouse'
    month_ago = "now() - INTERVAL 30 DAY"
    day_expression = 'toDate(occurred_at)'

    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        import clickhouse_connect

        self.database = database
        self.table = f'{database}.events'
        self._client = clickhouse_connect.get_client(
            host=host, port=port, username=user, password=password,
            # Запросы на полном объёме идут десятки секунд — таймаут должен
            # быть больше, иначе замерим таймаут, а не хранилище.
            send_receive_timeout=600,
        )

    def prepare(self) -> None:
        schema = CLICKHOUSE_SCHEMA.format(database=self.database)
        for statement in filter(None, (part.strip() for part in schema.split(';'))):
            self._client.command(statement)

    def drop(self) -> None:
        self._client.command(f'DROP TABLE IF EXISTS {self.table}')  # noqa: S608

    def insert(self, rows: Sequence[Sequence[Any]]) -> None:
        self._client.insert(self.table, rows, column_names=list(COLUMNS))

    def execute(self, sql: str) -> int:
        return len(self._client.query(sql).result_rows)

    def count(self) -> int:
        return int(self._client.query(f'SELECT count() FROM {self.table}').result_rows[0][0])  # noqa: S608

    def size_mb(self) -> float:
        # Имя базы приходит из нашей же настройки, а не снаружи, — отсюда noqa.
        where = f"active AND database = '{self.database}' AND table = 'events'"
        sql = f'SELECT sum(bytes_on_disk) FROM system.parts WHERE {where}'  # noqa: S608
        rows = self._client.query(sql).result_rows
        return float(rows[0][0] or 0) / 1024 / 1024

    def close(self) -> None:
        self._client.close()


class VerticaStorage(Storage):
    """Vertica через её собственный протокол."""

    name = 'Vertica'
    month_ago = "NOW() - INTERVAL '30 days'"
    day_expression = 'occurred_at::DATE'

    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        import vertica_python

        self.schema = database
        self.table = f'{database}.events'
        self._connection = vertica_python.connect(
            host=host, port=port, user=user, password=password,
            database='VMart', autocommit=True,
        )

    def prepare(self) -> None:
        with self._connection.cursor() as cursor:
            schema = VERTICA_SCHEMA.format(database=self.schema)
            for statement in filter(None, (part.strip() for part in schema.split(';'))):
                cursor.execute(statement)

    def drop(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS {self.table} CASCADE')  # noqa: S608

    def insert(self, rows: Sequence[Sequence[Any]]) -> None:
        placeholders = ', '.join(['%s'] * len(COLUMNS))
        with self._connection.cursor() as cursor:
            # executemany с use_prepared_statements=False Vertica превращает в
            # один COPY-подобный запрос: строка за строкой была бы на порядок
            # медленнее и сравнение вышло бы нечестным.
            sql = f'INSERT INTO {self.table} ({", ".join(COLUMNS)}) VALUES ({placeholders})'  # noqa: S608
            cursor.executemany(sql, [list(row) for row in rows], use_prepared_statements=False)

    def execute(self, sql: str) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(sql)
            return len(cursor.fetchall())

    def count(self) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(f'SELECT count(*) FROM {self.table}')  # noqa: S608
            return int(cursor.fetchone()[0])

    def size_mb(self) -> float:
        with self._connection.cursor() as cursor:
            where = f"anchor_table_schema = '{self.schema}' AND anchor_table_name = 'events'"
            cursor.execute(f'SELECT sum(used_bytes) FROM v_monitor.projection_storage WHERE {where}')  # noqa: S608
            used = cursor.fetchone()[0]
        return float(used or 0) / 1024 / 1024

    def close(self) -> None:
        self._connection.close()
