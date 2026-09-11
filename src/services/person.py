from functools import lru_cache

from elasticsearch import AsyncElasticsearch
from fastapi import Depends
from redis.asyncio import Redis

from db.elastic import get_elastic
from db.redis import get_redis
from models.person import Person
from services.base import BaseService, Pagination
from services.cache import RedisCache


class PersonService(BaseService[Person]):
    index = 'persons'
    model = Person

    async def search(self, query: str, pagination: Pagination) -> list[Person]:
        """Поиск персон по имени, сортировка по релевантности."""
        es_query = {'match': {'full_name': {'query': query, 'fuzziness': 'AUTO'}}}
        return await self._search(Person, pagination, query=es_query)


@lru_cache()
def get_person_service(
    redis: Redis = Depends(get_redis),
    elastic: AsyncElasticsearch = Depends(get_elastic),
) -> PersonService:
    return PersonService(elastic, RedisCache(redis))
