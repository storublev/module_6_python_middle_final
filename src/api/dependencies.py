"""Сборка сервисов для эндпоинтов (Composition Root).

Только здесь выбираются конкретные реализации хранилищ. Сервисы получают их
через конструктор и зависят от интерфейсов, поэтому ничего не знают ни об
Elasticsearch и Redis, ни о FastAPI. FastAPI создаёт зависимость один раз на
запрос: эндпоинт с двумя сервисами получит общие хранилище и кеш.
"""

from typing import Annotated

from elasticsearch import AsyncElasticsearch
from fastapi import Depends
from redis.asyncio import Redis

from core.config import settings
from db.elastic import get_elastic
from db.redis import get_redis
from services.cache import ModelCache
from services.film import FilmService
from services.genre import GenreService
from services.person import PersonService
from storage.base import DocumentStorage
from storage.elastic import BackoffPolicy, ElasticStorage
from storage.redis import RedisCache

ELASTIC_BACKOFF = BackoffPolicy(
    max_time=settings.elastic_backoff_max_time,
    factor=settings.elastic_backoff_factor,
    max_value=settings.elastic_backoff_max_value,
)


def get_storage(elastic: Annotated[AsyncElasticsearch, Depends(get_elastic)]) -> DocumentStorage:
    return ElasticStorage(elastic, retry=ELASTIC_BACKOFF)


def get_cache(redis: Annotated[Redis, Depends(get_redis)]) -> ModelCache:
    return ModelCache(RedisCache(redis), expire=settings.cache_expire_in_seconds)


StorageDep = Annotated[DocumentStorage, Depends(get_storage)]
CacheDep = Annotated[ModelCache, Depends(get_cache)]


def get_film_service(storage: StorageDep, cache: CacheDep) -> FilmService:
    return FilmService(storage, cache)


def get_genre_service(storage: StorageDep, cache: CacheDep) -> GenreService:
    return GenreService(storage, cache)


def get_person_service(storage: StorageDep, cache: CacheDep) -> PersonService:
    return PersonService(storage, cache)


FilmServiceDep = Annotated[FilmService, Depends(get_film_service)]
GenreServiceDep = Annotated[GenreService, Depends(get_genre_service)]
PersonServiceDep = Annotated[PersonService, Depends(get_person_service)]
