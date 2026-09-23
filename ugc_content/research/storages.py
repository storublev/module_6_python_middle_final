"""Два хранилища за одним интерфейсом: MongoDB и PostgreSQL.

Сравнивать их можно только на одинаковых условиях: одни и те же данные, одни и
те же сценарии чтения, один и тот же размер пачки при вставке. Поэтому
различия спрятаны здесь, а сам замер (bench.py) о них не знает и потому не
может нечаянно дать одному из хранилищ фору.

Схемы **не одинаковые, а равноценные**: каждому хранилищу даны индексы,
которые оно само считает правильными для этих запросов. Мерить MongoDB без
индексов против PostgreSQL с индексами — значит мерить не хранилища.

| Сценарий | MongoDB | PostgreSQL |
|---|---|---|
| Агрегат по фильму | индекс `film_id`, `$group` в конвейере | индекс `film_id`, `count(*) filter` и `avg` |
| Понравившиеся зрителю | составной индекс `(user_id, rating)` | частичный индекс по `rating >= 6` |
| Закладки зрителя | составной индекс `(user_id, created_at)` | составной индекс `(user_id, created_at)` |
| Рецензии фильма | по индексу на каждую сортировку | по индексу на каждую сортировку |

Ключи уникальности тоже одинаковые по смыслу: у зрителя одна оценка на фильм
и одна закладка на фильм. В MongoDB это уникальный составной индекс, в
PostgreSQL — первичный ключ.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any
from uuid import UUID

# Порог «понравилось»: оценка 6 и выше. Число одно на оба хранилища — иначе
# они отвечали бы на разные вопросы.
LIKED_FROM = 6

SORTS = ('newest', 'oldest', 'most_useful', 'highest_rating')

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS likes (
    film_id    uuid        NOT NULL,
    user_id    uuid        NOT NULL,
    rating     smallint    NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (film_id, user_id)
);
-- Частичный индекс: список «понравившихся» спрашивают только про оценки от 6,
-- и класть в индекс дизлайки незачем — он станет вдвое толще без пользы.
CREATE INDEX IF NOT EXISTS likes_user_liked_idx ON likes (user_id, created_at DESC)
    WHERE rating >= 6;

CREATE TABLE IF NOT EXISTS bookmarks (
    user_id    uuid        NOT NULL,
    film_id    uuid        NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, film_id)
);
CREATE INDEX IF NOT EXISTS bookmarks_user_created_idx ON bookmarks (user_id, created_at);

CREATE TABLE IF NOT EXISTS reviews (
    review_id  uuid        PRIMARY KEY,
    film_id    uuid        NOT NULL,
    user_id    uuid        NOT NULL,
    body       text        NOT NULL,
    rating     smallint,
    useful     integer     NOT NULL DEFAULT 0,
    useless    integer     NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_film_created_idx ON reviews (film_id, created_at DESC);
CREATE INDEX IF NOT EXISTS reviews_film_useful_idx ON reviews (film_id, useful DESC);
CREATE INDEX IF NOT EXISTS reviews_film_rating_idx ON reviews (film_id, rating DESC);

-- Готовый агрегат по фильму. Нужен потому, что считать его на каждом открытии
-- карточки нельзя: у популярного фильма сотни тысяч оценок, и подсчёт идёт
-- секунды в любом хранилище (см. README, раздел про счётчики).
CREATE TABLE IF NOT EXISTS film_ratings (
    film_id    uuid    PRIMARY KEY,
    likes      integer NOT NULL,
    dislikes   integer NOT NULL,
    sum_rating bigint  NOT NULL,
    votes      bigint  NOT NULL
);
"""

POSTGRES_QUERIES = {
    'film_rating': """
        SELECT count(*) FILTER (WHERE rating >= %(liked)s) AS likes,
               count(*) FILTER (WHERE rating < %(liked)s)  AS dislikes,
               avg(rating)                                 AS average
        FROM likes WHERE film_id = %(film_id)s
    """,
    'liked_films': """
        SELECT film_id, rating, created_at FROM likes
        WHERE user_id = %(user_id)s AND rating >= %(liked)s
        ORDER BY created_at DESC LIMIT %(limit)s
    """,
    'bookmarks': """
        SELECT film_id, created_at FROM bookmarks
        WHERE user_id = %(user_id)s ORDER BY created_at LIMIT %(limit)s
    """,
    'reviews_newest': """
        SELECT review_id, user_id, rating, useful, created_at FROM reviews
        WHERE film_id = %(film_id)s ORDER BY created_at DESC LIMIT %(limit)s
    """,
    'film_rating_counter': """
        SELECT likes, dislikes, sum_rating, votes FROM film_ratings WHERE film_id = %(film_id)s
    """,
    'build_counters': """
        INSERT INTO film_ratings (film_id, likes, dislikes, sum_rating, votes)
        SELECT film_id,
               count(*) FILTER (WHERE rating >= 6),
               count(*) FILTER (WHERE rating < 6),
               sum(rating),
               count(*)
        FROM likes GROUP BY film_id
        ON CONFLICT (film_id) DO UPDATE SET likes = EXCLUDED.likes, dislikes = EXCLUDED.dislikes,
                                            sum_rating = EXCLUDED.sum_rating, votes = EXCLUDED.votes
    """,
    'reviews_most_useful': """
        SELECT review_id, user_id, rating, useful, created_at FROM reviews
        WHERE film_id = %(film_id)s ORDER BY useful DESC LIMIT %(limit)s
    """,
}


class Storage(ABC):
    """Хранилище пользовательского контента для исследования."""

    name: str

    @abstractmethod
    def prepare(self) -> None:
        """Создаёт схему и индексы."""

    @abstractmethod
    def drop(self) -> None:
        """Удаляет данные исследования: прогон должен начинаться с чистого листа."""

    @abstractmethod
    def insert_likes(self, rows: Sequence[Sequence[Any]]) -> None:
        """Вставляет пачку оценок."""

    @abstractmethod
    def insert_bookmarks(self, rows: Sequence[Sequence[Any]]) -> None:
        """Вставляет пачку закладок."""

    @abstractmethod
    def insert_reviews(self, rows: Sequence[Sequence[Any]]) -> None:
        """Вставляет пачку рецензий."""

    @abstractmethod
    def set_rating(self, film_id: UUID, user_id: UUID, rating: int, created_at: Any) -> None:
        """Ставит или заменяет одну оценку — как это делал бы сервис."""

    @abstractmethod
    def film_rating(self, film_id: UUID) -> tuple[int, int, float | None]:
        """Считает агрегат по фильму по всем его оценкам: лайки, дизлайки, среднее."""

    @abstractmethod
    def build_counters(self) -> None:
        """Считает готовые агрегаты по всем фильмам и складывает их рядом.

        Это то, что сервис в бою делает не пачкой, а по одной записи на каждую
        оценку. Здесь агрегаты строятся разом, чтобы было с чем сравнивать
        подсчёт на лету.
        """

    @abstractmethod
    def film_rating_counter(self, film_id: UUID) -> tuple[int, int, float | None]:
        """Читает готовый агрегат по фильму — одна запись по первичному ключу."""

    @abstractmethod
    def liked_films(self, user_id: UUID, limit: int) -> int:
        """Отдаёт число записей на странице понравившихся фильмов зрителя."""

    @abstractmethod
    def bookmarks(self, user_id: UUID, limit: int) -> int:
        """Отдаёт число записей на странице закладок зрителя."""

    @abstractmethod
    def reviews(self, film_id: UUID, sort: str, limit: int) -> int:
        """Отдаёт число записей на странице рецензий фильма."""

    def after_load(self) -> None:  # noqa: B027 — необязательный хук: у MongoDB его нет
        """Что хранилище делает после загрузки данных, до замеров.

        PostgreSQL здесь обновляет статистику (ANALYZE): без неё планировщик
        считает таблицы пустыми и выбирает планы наугад. MongoDB статистику не
        собирает, поэтому у неё это пустая операция. Это не фора PostgreSQL, а
        выравнивание условий: в бою ANALYZE выполняет autovacuum сам.
        """

    @abstractmethod
    def counts(self) -> dict[str, int]:
        """Сколько записей в каждой коллекции или таблице."""

    @abstractmethod
    def size_mb(self) -> float:
        """Сколько места занимают данные вместе с индексами, МБ."""

    @abstractmethod
    def close(self) -> None:
        """Закрывает соединение."""


class MongoStorage(Storage):
    """MongoDB: три коллекции, агрегат считается конвейером."""

    name = 'mongodb'

    def __init__(self, uri: str, database: str) -> None:
        from pymongo import MongoClient

        # uuidRepresentation='standard' — иначе pymongo пишет UUID в старом
        # формате драйвера, и данные нельзя прочитать ничем, кроме pymongo.
        self._client: Any = MongoClient(uri, uuidRepresentation='standard')
        self._db = self._client[database]

    def prepare(self) -> None:
        from pymongo import ASCENDING, DESCENDING

        self._db.likes.create_index([('film_id', ASCENDING), ('user_id', ASCENDING)], unique=True)
        self._db.likes.create_index([('user_id', ASCENDING), ('rating', DESCENDING), ('created_at', DESCENDING)])
        self._db.bookmarks.create_index([('user_id', ASCENDING), ('film_id', ASCENDING)], unique=True)
        self._db.bookmarks.create_index([('user_id', ASCENDING), ('created_at', ASCENDING)])
        self._db.reviews.create_index([('film_id', ASCENDING), ('created_at', DESCENDING)])
        self._db.reviews.create_index([('film_id', ASCENDING), ('useful', DESCENDING)])
        self._db.reviews.create_index([('film_id', ASCENDING), ('rating', DESCENDING)])

    def drop(self) -> None:
        for collection in ('likes', 'bookmarks', 'reviews', 'film_ratings'):
            self._db[collection].drop()

    def insert_likes(self, rows: Sequence[Sequence[Any]]) -> None:
        documents = [
            {'film_id': film_id, 'user_id': user_id, 'rating': rating, 'created_at': created_at}
            for film_id, user_id, rating, created_at in rows
        ]
        # ordered=False — при дубле ключа вставка продолжается, а не обрывает
        # пачку на первой же записи.
        self._db.likes.insert_many(documents, ordered=False)

    def insert_bookmarks(self, rows: Sequence[Sequence[Any]]) -> None:
        documents = [
            {'user_id': user_id, 'film_id': film_id, 'created_at': created_at}
            for user_id, film_id, created_at in rows
        ]
        self._db.bookmarks.insert_many(documents, ordered=False)

    def insert_reviews(self, rows: Sequence[Sequence[Any]]) -> None:
        documents = [
            {
                '_id': review_id, 'film_id': film_id, 'user_id': user_id, 'body': body,
                'rating': rating, 'useful': useful, 'useless': useless, 'created_at': created_at,
            }
            for review_id, film_id, user_id, body, rating, useful, useless, created_at in rows
        ]
        self._db.reviews.insert_many(documents, ordered=False)

    def set_rating(self, film_id: UUID, user_id: UUID, rating: int, created_at: Any) -> None:
        self._db.likes.update_one(
            {'film_id': film_id, 'user_id': user_id},
            {'$set': {'rating': rating, 'created_at': created_at}},
            upsert=True,
        )

    def film_rating(self, film_id: UUID) -> tuple[int, int, float | None]:
        pipeline = [
            {'$match': {'film_id': film_id}},
            {'$group': {
                '_id': None,
                'likes': {'$sum': {'$cond': [{'$gte': ['$rating', LIKED_FROM]}, 1, 0]}},
                'dislikes': {'$sum': {'$cond': [{'$lt': ['$rating', LIKED_FROM]}, 1, 0]}},
                'average': {'$avg': '$rating'},
            }},
        ]
        result = list(self._db.likes.aggregate(pipeline))
        if not result:
            return 0, 0, None
        row = result[0]
        return row['likes'], row['dislikes'], row['average']

    def build_counters(self) -> None:
        # $merge складывает результат группировки прямо в коллекцию: гонять
        # десять миллионов промежуточных документов через клиент незачем.
        pipeline = [
            {'$group': {
                '_id': '$film_id',
                'likes': {'$sum': {'$cond': [{'$gte': ['$rating', LIKED_FROM]}, 1, 0]}},
                'dislikes': {'$sum': {'$cond': [{'$lt': ['$rating', LIKED_FROM]}, 1, 0]}},
                'sum_rating': {'$sum': '$rating'},
                'votes': {'$sum': 1},
            }},
            {'$merge': {'into': 'film_ratings', 'on': '_id', 'whenMatched': 'replace'}},
        ]
        self._db.likes.aggregate(pipeline, allowDiskUse=True)

    def film_rating_counter(self, film_id: UUID) -> tuple[int, int, float | None]:
        document = self._db.film_ratings.find_one({'_id': film_id})
        if not document:
            return 0, 0, None
        votes = document['votes']
        average = document['sum_rating'] / votes if votes else None
        return document['likes'], document['dislikes'], average

    def liked_films(self, user_id: UUID, limit: int) -> int:
        cursor = self._db.likes.find(
            {'user_id': user_id, 'rating': {'$gte': LIKED_FROM}},
            {'film_id': 1, 'rating': 1, 'created_at': 1},
        ).sort('created_at', -1).limit(limit)
        return len(list(cursor))

    def bookmarks(self, user_id: UUID, limit: int) -> int:
        cursor = self._db.bookmarks.find(
            {'user_id': user_id}, {'film_id': 1, 'created_at': 1},
        ).sort('created_at', 1).limit(limit)
        return len(list(cursor))

    def reviews(self, film_id: UUID, sort: str, limit: int) -> int:
        order = {'newest': ('created_at', -1), 'oldest': ('created_at', 1),
                 'most_useful': ('useful', -1), 'highest_rating': ('rating', -1)}[sort]
        cursor = self._db.reviews.find(
            {'film_id': film_id}, {'body': 0},
        ).sort(*order).limit(limit)
        return len(list(cursor))

    def counts(self) -> dict[str, int]:
        return {name: self._db[name].estimated_document_count() for name in ('likes', 'bookmarks', 'reviews')}

    def size_mb(self) -> float:
        stats = self._db.command('dbstats')
        return round((stats['dataSize'] + stats['indexSize']) / 1024 / 1024, 1)

    def close(self) -> None:
        self._client.close()


class PostgresStorage(Storage):
    """PostgreSQL: три таблицы, агрегат считается SQL."""

    name = 'postgresql'

    def __init__(self, dsn: str) -> None:
        import psycopg

        self._connection: Any = psycopg.connect(dsn, autocommit=True)

    def prepare(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(POSTGRES_SCHEMA)

    def drop(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute('DROP TABLE IF EXISTS likes, bookmarks, reviews, film_ratings')

    def insert_likes(self, rows: Sequence[Sequence[Any]]) -> None:
        # COPY — родной способ PostgreSQL загружать данные пачкой; INSERT на
        # десяти миллионах строк был бы медленнее в разы, и сравнение вышло бы
        # нечестным не в пользу PostgreSQL.
        with self._connection.cursor() as cursor:
            with cursor.copy('COPY likes (film_id, user_id, rating, created_at) FROM STDIN') as copy:
                for row in rows:
                    copy.write_row(row)

    def insert_bookmarks(self, rows: Sequence[Sequence[Any]]) -> None:
        with self._connection.cursor() as cursor:
            with cursor.copy('COPY bookmarks (user_id, film_id, created_at) FROM STDIN') as copy:
                for row in rows:
                    copy.write_row(row)

    def insert_reviews(self, rows: Sequence[Sequence[Any]]) -> None:
        columns = 'review_id, film_id, user_id, body, rating, useful, useless, created_at'
        with self._connection.cursor() as cursor:
            with cursor.copy(f'COPY reviews ({columns}) FROM STDIN') as copy:
                for row in rows:
                    copy.write_row(row)

    def set_rating(self, film_id: UUID, user_id: UUID, rating: int, created_at: Any) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO likes (film_id, user_id, rating, created_at) VALUES (%s, %s, %s, %s) '
                'ON CONFLICT (film_id, user_id) DO UPDATE SET rating = EXCLUDED.rating, '
                'created_at = EXCLUDED.created_at',
                (film_id, user_id, rating, created_at),
            )

    def film_rating(self, film_id: UUID) -> tuple[int, int, float | None]:
        with self._connection.cursor() as cursor:
            cursor.execute(POSTGRES_QUERIES['film_rating'], {'film_id': film_id, 'liked': LIKED_FROM})
            likes, dislikes, average = cursor.fetchone()
        return likes, dislikes, float(average) if average is not None else None

    def build_counters(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(POSTGRES_QUERIES['build_counters'])
            cursor.execute('ANALYZE film_ratings')

    def film_rating_counter(self, film_id: UUID) -> tuple[int, int, float | None]:
        with self._connection.cursor() as cursor:
            cursor.execute(POSTGRES_QUERIES['film_rating_counter'], {'film_id': film_id})
            row = cursor.fetchone()
        if row is None:
            return 0, 0, None
        likes, dislikes, sum_rating, votes = row
        return likes, dislikes, (sum_rating / votes if votes else None)

    def liked_films(self, user_id: UUID, limit: int) -> int:
        return self._fetch('liked_films', {'user_id': user_id, 'liked': LIKED_FROM, 'limit': limit})

    def bookmarks(self, user_id: UUID, limit: int) -> int:
        return self._fetch('bookmarks', {'user_id': user_id, 'limit': limit})

    def reviews(self, film_id: UUID, sort: str, limit: int) -> int:
        query = {'newest': 'reviews_newest', 'most_useful': 'reviews_most_useful'}.get(sort)
        if query is None:
            sql = POSTGRES_QUERIES['reviews_newest'].replace(
                'ORDER BY created_at DESC',
                'ORDER BY created_at ASC' if sort == 'oldest' else 'ORDER BY rating DESC',
            )
            return self._fetch_sql(sql, {'film_id': film_id, 'limit': limit})
        return self._fetch(query, {'film_id': film_id, 'limit': limit})

    def _fetch(self, query: str, params: dict[str, Any]) -> int:
        return self._fetch_sql(POSTGRES_QUERIES[query], params)

    def _fetch_sql(self, sql: str, params: dict[str, Any]) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            return len(cursor.fetchall())

    def after_load(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute('ANALYZE likes, bookmarks, reviews')

    def counts(self) -> dict[str, int]:
        counts = {}
        with self._connection.cursor() as cursor:
            for table in ('likes', 'bookmarks', 'reviews'):
                # Оценка по статистике планировщика, а не count(*): точный
                # подсчёт десяти миллионов строк — это отдельный полный скан.
                # Минус единица означает «статистики ещё нет» — тогда считаем
                # честно, иначе в отчёт попало бы -1.
                cursor.execute('SELECT reltuples::bigint FROM pg_class WHERE relname = %s', (table,))
                estimate = int(cursor.fetchone()[0])
                if estimate < 0:
                    cursor.execute(f'SELECT count(*) FROM {table}')  # noqa: S608 — имя таблицы из списка выше
                    estimate = int(cursor.fetchone()[0])
                counts[table] = estimate
        return counts

    def size_mb(self) -> float:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT sum(pg_total_relation_size(relid)) FROM pg_catalog.pg_statio_user_tables "
                "WHERE relname IN ('likes', 'bookmarks', 'reviews', 'film_ratings')"
            )
            total = cursor.fetchone()[0] or 0
        return round(total / 1024 / 1024, 1)

    def close(self) -> None:
        self._connection.close()
