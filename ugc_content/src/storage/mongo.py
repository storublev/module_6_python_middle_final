"""Реализация хранилища на MongoDB.

Всё знание о MongoDB собрано здесь: конвейеры агрегации, upsert'ы, коды
ошибок драйвера. Слой `services` ничего этого не видит — он работает с
интерфейсами из `storage/base.py`.

Общее правило модуля: любая ошибка драйвера превращается в
`StorageUnavailableError`. Наружу не должно вылетать `PyMongoError` —
иначе API ответил бы 500 вместо 503, а клиент не понял бы, что запрос можно
повторить.
"""

import asyncio
import functools
import logging
import random
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timezone
from typing import Any, ParamSpec, TypeVar
from uuid import UUID

from beanie import init_beanie
from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError, OperationFailure, PyMongoError

from core.config import settings
from models.content import Bookmark, FilmRating, Like, Page, Review, ReviewSort
from storage.base import (
    LIKED_FROM,
    BookmarkStorage,
    HealthCheck,
    LikeStorage,
    ReviewStorage,
    StorageUnavailableError,
)
from storage.documents import (
    DOCUMENTS,
    BookmarkDocument,
    FilmRatingDocument,
    LikeDocument,
    ReviewDocument,
    ReviewVoteDocument,
    utc_now,
)

logger = logging.getLogger(__name__)

P = ParamSpec('P')
T = TypeVar('T')

# Клиент запоминается при подключении: транзакция открывается через сессию, а
# сессию выдаёт клиент. Beanie отдаёт коллекции, но не клиент, поэтому держим
# его здесь — в одном месте и только для этого.
_client: AsyncMongoClient | None = None
# Сколько раз повторять транзакцию, если две параллельные записи столкнулись
# на одном документе. MongoDB помечает такую ошибку как TransientTransactionError
# и прямо предлагает повторить.
#
# Повторять нужно с паузой, а не подряд: у популярного фильма счётчик один на
# всех, и десяток одновременных оценок бьётся именно за него. Без пауз
# соперники расходятся и сталкиваются снова — проверено тестом
# `test_parallel_ratings_keep_counter_exact`, где три мгновенных повтора
# оставляли часть запросов без ответа.
TRANSACTION_ATTEMPTS = 6
# Пауза удваивается: 20 мс, 40, 80… Случайная добавка разводит соперников,
# иначе они просыпаются одновременно и снова конфликтуют.
TRANSACTION_RETRY_DELAY = 0.02

# Во что превращается порядок сортировки рецензий. Пары «поле, направление» —
# ровно те, под которые заведены индексы в documents.py: сортировка без
# индекса на миллионах рецензий означала бы сортировку в памяти MongoDB.
REVIEW_SORTS: dict[ReviewSort, list[tuple[str, int]]] = {
    ReviewSort.NEWEST: [('created_at', -1)],
    ReviewSort.OLDEST: [('created_at', 1)],
    ReviewSort.MOST_USEFUL: [('useful', -1)],
    ReviewSort.HIGHEST_RATING: [('rating', -1)],
}


def aware(moment: datetime) -> datetime:
    """Возвращает время с часовым поясом.

    MongoDB хранит даты как UTC, но отдаёт их без пояса, а контракт API
    требует пояс явно: «2026-09-22T12:00:00» без него — это неизвестно какое
    время, и клиент в другом поясе покажет его неправильно.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def translate_errors(method: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, Coroutine[Any, Any, T]]:
    """Превращает ошибки драйвера в `StorageUnavailableError`.

    Декоратор, а не try/except в каждом методе: забыть обернуть один метод
    проще, чем забыть повесить декоратор, и цена забывчивости — 500 вместо
    503 у одного эндпоинта.
    """

    @functools.wraps(method)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await method(*args, **kwargs)
        except DuplicateKeyError:
            # Нарушение уникальности — не сбой хранилища, а бизнес-ситуация:
            # её разбирает сам метод, сюда она попадать не должна.
            raise
        except PyMongoError as error:
            logger.error('MongoDB не ответила: %s', error)
            raise StorageUnavailableError(str(error)) from error

    return wrapper


async def connect(uri: str, database: str, **options: Any) -> AsyncMongoClient:
    """Открывает соединение и привязывает документы к базе.

    `uuidRepresentation='standard'` — иначе драйвер пишет UUID в своём старом
    формате, и данные нельзя прочитать ничем, кроме pymongo.
    """
    global _client

    client: AsyncMongoClient = AsyncMongoClient(uri, uuidRepresentation='standard', **options)
    # allow_index_dropping: индексы описаны только в documents.py, и то, чего
    # там больше нет, должно исчезнуть и в базе. Иначе снятый индекс остаётся
    # навсегда и берёт свою цену с каждой записи, ничего не ускоряя.
    await init_beanie(database=client[database], document_models=DOCUMENTS, allow_index_dropping=True)
    _client = client
    return client


async def in_transaction(action: Callable[[AsyncClientSession], Awaitable[T]]) -> T:
    """Выполняет несколько записей одной транзакцией, повторяя её при конфликте.

    Зачем это нужно. Оценка и счётчик фильма — две записи, и между ними
    процесс может умереть. Без транзакции счётчик разойдётся с оценками, а
    повтор того же запроса делу не поможет: оценка уже сохранена, и второй
    заход просто увидит её на месте и ничего не исправит. Внутри транзакции
    видны либо обе записи, либо ни одной.

    Повтор при `TransientTransactionError` — не перестраховка, а часть
    протокола: две параллельные транзакции, тронувшие один документ, штатно
    завершаются конфликтом, и MongoDB ожидает, что клиент повторит.

    Требуется набор реплик; на одиночном mongod транзакций не существует.
    """
    if _client is None:
        raise StorageUnavailableError('Соединение с MongoDB не открыто')

    last_error: OperationFailure | None = None
    for attempt in range(TRANSACTION_ATTEMPTS):
        async with _client.start_session() as session:
            try:
                # start_transaction у асинхронного драйвера — корутина,
                # которая возвращает менеджер контекста: сначала await, потом
                # async with.
                async with await session.start_transaction():
                    return await action(session)
            except OperationFailure as error:
                if not error.has_error_label('TransientTransactionError'):
                    raise
                last_error = error
                logger.warning('Транзакция столкнулась с конфликтом, попытка %d', attempt + 1)
        # Пауза растёт, чтобы соперники разъехались во времени. Случайная
        # добавка — не криптография, поэтому random здесь уместен.
        delay = TRANSACTION_RETRY_DELAY * 2 ** attempt
        await asyncio.sleep(delay * random.uniform(0.5, 1.5))  # noqa: S311
    logger.error('Транзакция не прошла за %d попыток: %s', TRANSACTION_ATTEMPTS, last_error)
    raise StorageUnavailableError(f'Транзакция не прошла за {TRANSACTION_ATTEMPTS} попыток')


async def count_capped(collection: Any, query: dict[str, Any], limit: int) -> int:
    """Считает записи, но не дороже `limit`.

    Точный `count_documents` обходит всю выборку: у фильма с сотней тысяч
    рецензий это работа на каждое открытие страницы, причём ради числа,
    которое показывается мелким шрифтом. Поэтому считаем не больше предела, а
    выше него отдаём сам предел — клиенту достаточно знать, что записей
    «больше тысячи».
    """
    return await collection.count_documents(query, limit=limit)


class MongoLikeStorage(LikeStorage):
    """Оценки фильмов в коллекции `likes`."""

    @translate_errors
    async def set_rating(self, film_id: UUID, user_id: UUID, rating: int) -> Like:
        async def write(session: AsyncClientSession) -> Like:
            now = utc_now()
            # Upsert одной операцией: проверять существование отдельным запросом
            # значило бы гонку — два параллельных запроса зрителя создали бы две
            # оценки, и уникальный индекс отклонил бы вторую.
            #
            # Документ забирается в состоянии ДО изменения: по прежней оценке
            # видно, на сколько двигать счётчик фильма.
            previous = await LikeDocument.get_pymongo_collection().find_one_and_update(
                {'film_id': film_id, 'user_id': user_id},
                {
                    '$set': {'rating': rating, 'updated_at': now},
                    '$setOnInsert': {'film_id': film_id, 'user_id': user_id, 'created_at': now},
                },
                upsert=True,
                return_document=ReturnDocument.BEFORE,
                session=session,
            )
            await self._move_counter(film_id, previous['rating'] if previous else None, rating, session)
            return Like(
                film_id=film_id,
                user_id=user_id,
                rating=rating,
                created_at=aware(previous['created_at']) if previous else now,
                updated_at=now,
            )

        # Оценка и счётчик — одна операция для того, кто их читает: либо
        # изменились обе записи, либо ни одна.
        return await in_transaction(write)

    @translate_errors
    async def remove_rating(self, film_id: UUID, user_id: UUID) -> bool:
        async def write(session: AsyncClientSession) -> bool:
            removed = await LikeDocument.get_pymongo_collection().find_one_and_delete(
                {'film_id': film_id, 'user_id': user_id},
                session=session,
            )
            if removed is None:
                return False
            await self._move_counter(film_id, removed['rating'], None, session)
            return True

        return await in_transaction(write)

    @staticmethod
    async def _move_counter(
        film_id: UUID,
        was: int | None,
        now: int | None,
        session: AsyncClientSession,
    ) -> None:
        """Двигает готовый агрегат фильма на разницу между старой и новой оценкой.

        Счётчик обновляется отдельной операцией, а не пересчитывается по
        коллекции: у популярного фильма сотни тысяч оценок, и пересчёт занял
        бы секунды (см. исследование). `$inc` же не зависит от их количества.

        Вызывается только внутри транзакции — отсюда обязательный `session`.
        Без неё падение между записью оценки и сдвигом счётчика оставило бы
        агрегат неверным навсегда: повтор запроса увидел бы уже сохранённую
        оценку и ничего не исправил.
        """
        changes: dict[str, int] = {'likes': 0, 'dislikes': 0, 'sum_rating': 0, 'votes': 0}
        if was is not None:
            changes['likes' if was >= LIKED_FROM else 'dislikes'] -= 1
            changes['sum_rating'] -= was
            changes['votes'] -= 1
        if now is not None:
            changes['likes' if now >= LIKED_FROM else 'dislikes'] += 1
            changes['sum_rating'] += now
            changes['votes'] += 1
        # Если не изменилось ничего (оценку переставили на ту же самую),
        # запись не нужна.
        if not any(changes.values()):
            return
        # Нулевые поля в $inc оставляем: так у нового документа сразу есть все
        # четыре счётчика, и читающему не приходится гадать, что значит их
        # отсутствие.
        await FilmRatingDocument.get_pymongo_collection().update_one(
            {'film_id': film_id},
            {'$inc': changes, '$setOnInsert': {'film_id': film_id}},
            upsert=True,
            session=session,
        )

    @translate_errors
    async def get_rating(self, film_id: UUID, user_id: UUID) -> Like | None:
        document = await LikeDocument.get_pymongo_collection().find_one(
            {'film_id': film_id, 'user_id': user_id},
        )
        return self._to_like(document) if document else None

    @translate_errors
    async def get_film_rating(self, film_id: UUID) -> FilmRating:
        # Одна запись по ключу вместо конвейера по всем оценкам фильма.
        # Разница не косметическая: на данных исследования подсчёт агрегата по
        # популярному фильму занимает секунды, а чтение счётчика — доли
        # миллисекунды (см. ugc_content/research/README.md).
        document = await FilmRatingDocument.get_pymongo_collection().find_one({'film_id': film_id})
        if not document:
            # Фильм без единой оценки — не ошибка: карточка показывает нули.
            return FilmRating(film_id=film_id, likes=0, dislikes=0, average_rating=None)
        votes = document.get('votes', 0)
        return FilmRating(
            film_id=film_id,
            likes=max(0, document.get('likes', 0)),
            dislikes=max(0, document.get('dislikes', 0)),
            average_rating=round(document.get('sum_rating', 0) / votes, 2) if votes else None,
        )

    @translate_errors
    async def list_liked_films(self, user_id: UUID, page: int, size: int) -> Page[Like]:
        query = {'user_id': user_id, 'rating': {'$gte': LIKED_FROM}}
        collection = LikeDocument.get_pymongo_collection()
        total = await count_capped(collection, query, settings.exact_count_limit)
        cursor = collection.find(query).sort('created_at', -1).skip((page - 1) * size).limit(size)
        items = [self._to_like(document) async for document in cursor]
        return Page[Like](items=items, total=total, page=page, size=size)

    @staticmethod
    def _to_like(document: dict[str, Any]) -> Like:
        return Like(
            film_id=document['film_id'],
            user_id=document['user_id'],
            rating=document['rating'],
            created_at=aware(document['created_at']),
            updated_at=aware(document.get('updated_at', document['created_at'])),
        )


class MongoBookmarkStorage(BookmarkStorage):
    """Закладки в коллекции `bookmarks`."""

    @translate_errors
    async def add(self, film_id: UUID, user_id: UUID) -> Bookmark:
        # Тот же upsert: повторное добавление не создаёт вторую закладку и не
        # меняет время — «добавить в закладки» идемпотентно (ФТ-9).
        document = await BookmarkDocument.get_pymongo_collection().find_one_and_update(
            {'user_id': user_id, 'film_id': film_id},
            {'$setOnInsert': {'user_id': user_id, 'film_id': film_id, 'created_at': utc_now()}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        # upsert=True с ReturnDocument.AFTER всегда отдаёт документ: его либо
        # нашли, либо только что создали.
        assert document is not None  # noqa: S101 — инвариант upsert, не проверка входных данных
        return self._to_bookmark(document)

    @translate_errors
    async def remove(self, film_id: UUID, user_id: UUID) -> bool:
        result = await BookmarkDocument.get_pymongo_collection().delete_one(
            {'user_id': user_id, 'film_id': film_id},
        )
        return result.deleted_count > 0

    @translate_errors
    async def list_for_user(self, user_id: UUID, page: int, size: int) -> Page[Bookmark]:
        collection = BookmarkDocument.get_pymongo_collection()
        total = await count_capped(collection, {'user_id': user_id}, settings.exact_count_limit)
        cursor = collection.find({'user_id': user_id}).sort('created_at', 1).skip((page - 1) * size).limit(size)
        items = [self._to_bookmark(document) async for document in cursor]
        return Page[Bookmark](items=items, total=total, page=page, size=size)

    @staticmethod
    def _to_bookmark(document: dict[str, Any]) -> Bookmark:
        return Bookmark(
            film_id=document['film_id'],
            user_id=document['user_id'],
            created_at=aware(document['created_at']),
        )


class MongoReviewStorage(ReviewStorage):
    """Рецензии и голоса за них."""

    @translate_errors
    async def add_review(self, film_id: UUID, user_id: UUID, text: str, rating: int | None) -> Review | None:
        document = ReviewDocument(film_id=film_id, user_id=user_id, text=text, rating=rating)
        try:
            await document.insert()
        except DuplicateKeyError:
            # Уникальный индекс (film_id, user_id): вторую рецензию на тот же
            # фильм писать нельзя. Это бизнес-правило, а не сбой.
            return None
        return self._to_review(document.model_dump())

    @translate_errors
    async def get_review(self, review_id: UUID) -> Review | None:
        document = await ReviewDocument.get_pymongo_collection().find_one({'review_id': review_id})
        return self._to_review(document) if document else None

    @translate_errors
    async def delete_review(self, review_id: UUID) -> bool:
        async def write(session: AsyncClientSession) -> bool:
            result = await ReviewDocument.get_pymongo_collection().delete_one(
                {'review_id': review_id}, session=session,
            )
            if result.deleted_count == 0:
                return False
            # Голоса за удалённую рецензию больше не нужны: они занимают место
            # и мешают тому, кто напишет рецензию заново. Удаляются они в той
            # же транзакции — иначе сбой между двумя операциями оставил бы
            # голоса за несуществующей рецензией, и убрать их обычным
            # сценарием сервиса было бы уже нельзя.
            await ReviewVoteDocument.get_pymongo_collection().delete_many(
                {'review_id': review_id}, session=session,
            )
            return True

        return await in_transaction(write)

    @translate_errors
    async def vote(self, review_id: UUID, user_id: UUID, useful: bool) -> Review | None:
        async def write(session: AsyncClientSession) -> Review | None:
            reviews = ReviewDocument.get_pymongo_collection()
            # Существование рецензии проверяется внутри транзакции: иначе
            # параллельное удаление успело бы пройти между проверкой и
            # записью, и голос остался бы за удалённой рецензией.
            if await reviews.find_one({'review_id': review_id}, {'_id': 1}, session=session) is None:
                return None

            previous = await ReviewVoteDocument.get_pymongo_collection().find_one_and_update(
                {'review_id': review_id, 'user_id': user_id},
                {'$set': {'useful': useful}, '$setOnInsert': {'created_at': utc_now()}},
                upsert=True,
                return_document=ReturnDocument.BEFORE,
                session=session,
            )
            changes = self._counter_changes(previous['useful'] if previous else None, useful)
            if not changes:
                # Зритель нажал то же самое второй раз — счётчики трогать не за что.
                document = await reviews.find_one({'review_id': review_id}, session=session)
                return self._to_review(document) if document else None

            document = await reviews.find_one_and_update(
                {'review_id': review_id},
                {'$inc': changes},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            return self._to_review(document) if document else None

        return await in_transaction(write)

    @translate_errors
    async def list_reviews(self, film_id: UUID, sort: ReviewSort, page: int, size: int) -> Page[Review]:
        collection = ReviewDocument.get_pymongo_collection()
        total = await count_capped(collection, {'film_id': film_id}, settings.exact_count_limit)
        cursor = (
            collection.find({'film_id': film_id})
            .sort(REVIEW_SORTS[sort])
            .skip((page - 1) * size)
            .limit(size)
        )
        items = [self._to_review(document) async for document in cursor]
        return Page[Review](items=items, total=total, page=page, size=size)

    @staticmethod
    def _counter_changes(previous: bool | None, useful: bool) -> dict[str, int]:
        """Насколько поменять счётчики: голос заменяется, а не добавляется (ФТ-7)."""
        if previous is None:
            return {'useful': 1} if useful else {'useless': 1}
        if previous == useful:
            return {}
        return {'useful': 1, 'useless': -1} if useful else {'useful': -1, 'useless': 1}

    @staticmethod
    def _to_review(document: dict[str, Any]) -> Review:
        return Review(
            review_id=document['review_id'],
            film_id=document['film_id'],
            user_id=document['user_id'],
            text=document['text'],
            rating=document.get('rating'),
            useful=document.get('useful', 0),
            useless=document.get('useless', 0),
            created_at=aware(document['created_at']),
        )


class MongoHealthCheck(HealthCheck):
    """Проверка готовности: отвечает ли MongoDB на ping."""

    def __init__(self, client: AsyncMongoClient) -> None:
        self._client = client

    async def is_ready(self) -> bool:
        try:
            await self._client.admin.command('ping')
        except PyMongoError as error:
            logger.warning('MongoDB не готова: %s', error)
            return False
        return True
