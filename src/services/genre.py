from functools import lru_cache

from elasticsearch import AsyncElasticsearch
from fastapi import Depends
from redis.asyncio import Redis

from db.elastic import get_elastic
from db.redis import get_redis
from models.genre import Genre
from services.base import BaseService, Pagination
from services.cache import RedisCache
from storage.base import Sort
from storage.elastic import ElasticStorage


class GenreService(BaseService[Genre]):
    index = 'genres'
    model = Genre

    async def get_list(self, pagination: Pagination) -> list[Genre]:
        """Список жанров в алфавитном порядке."""
        return await self._search(Genre, pagination, sort=(Sort('name'), Sort('id')))


@lru_cache()
def get_genre_service(
    redis: Redis = Depends(get_redis),
    elastic: AsyncElasticsearch = Depends(get_elastic),
) -> GenreService:
    return GenreService(ElasticStorage(elastic), RedisCache(redis))
