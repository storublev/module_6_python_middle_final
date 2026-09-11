from functools import lru_cache
from uuid import UUID

from elasticsearch import AsyncElasticsearch
from fastapi import Depends
from redis.asyncio import Redis

from db.elastic import get_elastic
from db.redis import get_redis
from models.film import Film, FilmShort
from services.base import BaseService, Pagination, nested_term, sort_by
from services.cache import RedisCache

PERSON_ROLES = ('actors', 'writers', 'directors')


class FilmService(BaseService[Film]):
    index = 'movies'
    model = Film

    async def get_list(
        self,
        pagination: Pagination,
        sort: str,
        genre_id: UUID | None = None,
    ) -> list[FilmShort]:
        """Список фильмов с сортировкой и необязательным фильтром по жанру."""
        query = None
        if genre_id:
            query = {'bool': {'filter': [nested_term('genres', 'id', genre_id)]}}
        return await self._search(FilmShort, pagination, query=query, sort=sort_by(sort))

    async def search(self, query: str, pagination: Pagination) -> list[FilmShort]:
        """Полнотекстовый поиск по названию и описанию, сортировка по релевантности."""
        es_query = {
            'multi_match': {
                'query': query,
                'fields': ['title^3', 'description'],
                'fuzziness': 'AUTO',
            },
        }
        return await self._search(FilmShort, pagination, query=es_query)

    async def get_by_person(self, person_id: UUID, pagination: Pagination, sort: str) -> list[FilmShort]:
        """Фильмы, в которых персона была актёром, сценаристом или режиссёром."""
        by_role = [nested_term(role, 'id', person_id) for role in PERSON_ROLES]
        query = {'bool': {'filter': [{'bool': {'should': by_role}}]}}
        return await self._search(FilmShort, pagination, query=query, sort=sort_by(sort))


@lru_cache()
def get_film_service(
    redis: Redis = Depends(get_redis),
    elastic: AsyncElasticsearch = Depends(get_elastic),
) -> FilmService:
    return FilmService(elastic, RedisCache(redis))
