from functools import lru_cache

from elasticsearch import AsyncElasticsearch
from fastapi import Depends
from redis.asyncio import Redis

from core.config import settings
from db.elastic import get_elastic
from db.redis import get_redis
from models.person import Person
from services.base import BaseService, Pagination
from services.cache import ModelCache
from storage.base import TextQuery
from storage.elastic import ElasticStorage
from storage.redis import RedisCache


class PersonService(BaseService[Person]):
    index = 'persons'
    model = Person

    async def search(self, query: str, pagination: Pagination) -> list[Person]:
        """Поиск персон по имени, сортировка по релевантности."""
        return await self._search(Person, pagination, text=TextQuery(query, ('full_name',)))


@lru_cache()
def get_person_service(
    redis: Redis = Depends(get_redis),
    elastic: AsyncElasticsearch = Depends(get_elastic),
) -> PersonService:
    cache = ModelCache(RedisCache(redis), expire=settings.cache_expire_in_seconds)
    return PersonService(ElasticStorage(elastic), cache)
