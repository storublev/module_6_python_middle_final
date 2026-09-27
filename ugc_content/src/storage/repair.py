"""Восстановление счётчиков по исходным записям.

Оценки и агрегат фильма пишутся одной транзакцией, голоса и счётчики рецензии
— тоже, поэтому новых расхождений появляться не должно. Но счётчик — это
производные данные, и они могут разойтись с исходными по причинам вне
транзакций: данные, залитые мимо сервиса (как в исследовании), правка руками
в аварийной ситуации, восстановление базы из резервной копии, сделанной в
момент записи.

Истина здесь всегда у исходных записей: оценки и голоса. Поэтому счётчики не
чинятся по частям, а пересчитываются из них целиком — конвейером на стороне
MongoDB, без выгрузки миллионов документов в сервис.

Запуск — командой в контейнере:

    docker compose exec ugc-content python -m storage.repair --check
    docker compose exec ugc-content python -m storage.repair --fix
"""

import argparse
import asyncio
import logging
import logging.config
import sys
from typing import Any

from core.config import settings
from core.logger import LOGGING
from storage.base import LIKED_FROM
from storage.documents import FilmRatingDocument, LikeDocument, ReviewDocument, ReviewVoteDocument
from storage.mongo import connect

logger = logging.getLogger(__name__)

# Пересчёт агрегатов по всем оценкам: группировка на стороне базы, результат
# сразу складывается в коллекцию счётчиков. Выгружать десять миллионов оценок
# в сервис ради этого незачем.
FILM_RATINGS_PIPELINE: list[dict[str, Any]] = [
    {'$group': {
        '_id': '$film_id',
        'likes': {'$sum': {'$cond': [{'$gte': ['$rating', LIKED_FROM]}, 1, 0]}},
        'dislikes': {'$sum': {'$cond': [{'$lt': ['$rating', LIKED_FROM]}, 1, 0]}},
        'sum_rating': {'$sum': '$rating'},
        'votes': {'$sum': 1},
    }},
    # `_id` группировки — это film_id, и его нужно и перенести в своё поле, и
    # убрать: иначе $merge попытается переписать `_id` найденного документа, а
    # менять его нельзя — запись целиком откатится.
    {'$set': {'film_id': '$_id'}},
    {'$unset': '_id'},
    {'$merge': {'into': 'film_ratings', 'on': 'film_id', 'whenMatched': 'replace'}},
]

# То же для рецензий: голоса за полезность лежат отдельной коллекцией, а их
# счётчики — в самой рецензии.
REVIEW_VOTES_PIPELINE: list[dict[str, Any]] = [
    {'$group': {
        '_id': '$review_id',
        'useful': {'$sum': {'$cond': ['$useful', 1, 0]}},
        'useless': {'$sum': {'$cond': ['$useful', 0, 1]}},
    }},
    {'$set': {'review_id': '$_id'}},
    {'$unset': '_id'},
    {'$merge': {'into': 'reviews', 'on': 'review_id', 'whenMatched': 'merge'}},
]


async def find_broken_film_ratings() -> list[dict[str, Any]]:
    """Ищет фильмы, у которых счётчик разошёлся с оценками."""
    pipeline: list[dict[str, Any]] = [
        {'$group': {
            '_id': '$film_id',
            'likes': {'$sum': {'$cond': [{'$gte': ['$rating', LIKED_FROM]}, 1, 0]}},
            'dislikes': {'$sum': {'$cond': [{'$lt': ['$rating', LIKED_FROM]}, 1, 0]}},
            'sum_rating': {'$sum': '$rating'},
            'votes': {'$sum': 1},
        }},
        {'$lookup': {
            'from': 'film_ratings', 'localField': '_id', 'foreignField': 'film_id', 'as': 'counter',
        }},
        {'$set': {'counter': {'$first': '$counter'}}},
        {'$match': {'$expr': {'$or': [
            {'$ne': ['$votes', {'$ifNull': ['$counter.votes', 0]}]},
            {'$ne': ['$likes', {'$ifNull': ['$counter.likes', 0]}]},
            {'$ne': ['$dislikes', {'$ifNull': ['$counter.dislikes', 0]}]},
            {'$ne': ['$sum_rating', {'$ifNull': ['$counter.sum_rating', 0]}]},
        ]}}},
        {'$limit': 100},
    ]
    # aggregate у асинхронного драйвера — корутина, которая отдаёт курсор.
    cursor = await LikeDocument.get_pymongo_collection().aggregate(pipeline)
    return await cursor.to_list(length=100)


async def find_orphan_votes() -> int:
    """Считает голоса, у которых больше нет рецензии.

    Появиться они могут только извне транзакций — например, после
    восстановления базы из копии, снятой в момент удаления рецензии.
    """
    pipeline: list[dict[str, Any]] = [
        {'$lookup': {
            'from': 'reviews', 'localField': 'review_id', 'foreignField': 'review_id', 'as': 'review',
        }},
        {'$match': {'review': []}},
        {'$count': 'orphans'},
    ]
    cursor = await ReviewVoteDocument.get_pymongo_collection().aggregate(pipeline)
    rows = await cursor.to_list(length=1)
    return rows[0]['orphans'] if rows else 0


async def rebuild_film_ratings() -> int:
    """Пересчитывает агрегаты всех фильмов по оценкам."""
    cursor = await LikeDocument.get_pymongo_collection().aggregate(FILM_RATINGS_PIPELINE)
    await cursor.to_list(length=None)
    return await FilmRatingDocument.get_pymongo_collection().count_documents({})


async def rebuild_review_counters() -> int:
    """Пересчитывает счётчики полезности всех рецензий по голосам."""
    cursor = await ReviewVoteDocument.get_pymongo_collection().aggregate(REVIEW_VOTES_PIPELINE)
    await cursor.to_list(length=None)
    # У рецензий без единого голоса конвейер ничего не трогает: обнуляем их
    # отдельно, иначе оставшийся от прежних времён счётчик так и застрянет.
    reviews = ReviewDocument.get_pymongo_collection()
    voted = await ReviewVoteDocument.get_pymongo_collection().distinct('review_id')
    result = await reviews.update_many(
        {'review_id': {'$nin': voted}, '$or': [{'useful': {'$ne': 0}}, {'useless': {'$ne': 0}}]},
        {'$set': {'useful': 0, 'useless': 0}},
    )
    return result.modified_count


async def remove_orphan_votes() -> int:
    """Удаляет голоса, оставшиеся без рецензии."""
    votes = ReviewVoteDocument.get_pymongo_collection()
    alive = await ReviewDocument.get_pymongo_collection().distinct('review_id')
    result = await votes.delete_many({'review_id': {'$nin': alive}})
    return result.deleted_count


async def run(fix: bool) -> int:
    """Проверяет согласованность, а с `--fix` — восстанавливает её.

    Возвращает код выхода: 1, если расхождения найдены и не исправлялись, —
    так команду можно поставить в регулярную проверку.
    """
    client = await connect(settings.mongo_uri, settings.mongo_database)
    try:
        broken = await find_broken_film_ratings()
        orphans = await find_orphan_votes()
        logger.info('Фильмов с разошедшимся счётчиком: %d, голосов без рецензии: %d', len(broken), orphans)
        for row in broken[:10]:
            logger.info('  фильм %s: по оценкам votes=%d, в счётчике %s',
                        row['_id'], row['votes'], (row.get('counter') or {}).get('votes'))

        if not fix:
            return 1 if broken or orphans else 0

        films = await rebuild_film_ratings()
        reviews = await rebuild_review_counters()
        removed = await remove_orphan_votes()
        logger.info('Пересчитано: агрегатов фильмов %d, обнулено рецензий %d, удалено голосов %d',
                    films, reviews, removed)
        return 0
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description='Проверка и восстановление счётчиков')
    parser.add_argument('--fix', action='store_true', help='не только найти расхождения, но и устранить их')
    parser.add_argument('--check', action='store_true', help='только проверить (по умолчанию)')
    arguments = parser.parse_args()

    logging.config.dictConfig(LOGGING)
    return asyncio.run(run(fix=arguments.fix))


if __name__ == '__main__':
    sys.exit(main())
