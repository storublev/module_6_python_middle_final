"""Сборка сервисов для эндпоинтов (Composition Root).

Только здесь выбираются конкретные реализации хранилищ и внешних сервисов.
Сервисы получают их через конструктор и зависят от интерфейсов, поэтому ничего
не знают ни об Elasticsearch, Redis и сервисе авторизации, ни о FastAPI.
FastAPI создаёт зависимость один раз на запрос: эндпоинт с двумя сервисами
получит общие хранилище и кеш.
"""

from typing import Annotated

import httpx
from elasticsearch import AsyncElasticsearch
from fastapi import Depends
from redis.asyncio import Redis

from api.security import TokenDep
from core.config import settings
from db.auth import get_auth_client
from db.elastic import get_elastic
from db.redis import get_redis
from services.access import Access, AccessService
from services.cache import ModelCache
from services.film import FilmService
from services.genre import GenreService
from services.person import PersonService
from storage.access import AccessGateway
from storage.auth import AuthAccessGateway
from storage.base import DocumentStorage
from storage.elastic import ElasticStorage
from storage.redis import RedisCache
from storage.resilience import BackoffPolicy, CircuitBreaker

ELASTIC_BACKOFF = BackoffPolicy(
    max_time=settings.elastic_backoff_max_time,
    factor=settings.elastic_backoff_factor,
    max_value=settings.elastic_backoff_max_value,
)
AUTH_BACKOFF = BackoffPolicy(
    max_time=settings.auth_backoff_max_time,
    factor=settings.auth_backoff_factor,
    max_value=settings.auth_backoff_max_value,
)
# Прерыватель общий для всех запросов процесса: создавайся он на запрос,
# счётчик сбоев обнулялся бы каждый раз и недоступность сервиса авторизации
# никогда бы не накопилась. Состояние у каждого воркера uvicorn своё.
AUTH_BREAKER = CircuitBreaker(
    failures=settings.auth_breaker_failures,
    reset_timeout=settings.auth_breaker_reset_timeout,
)


def get_storage(elastic: Annotated[AsyncElasticsearch, Depends(get_elastic)]) -> DocumentStorage:
    return ElasticStorage(elastic, retry=ELASTIC_BACKOFF)


def get_cache(redis: Annotated[Redis, Depends(get_redis)]) -> ModelCache:
    return ModelCache(RedisCache(redis), expire=settings.cache_expire_in_seconds)


def get_access_gateway(client: Annotated[httpx.AsyncClient, Depends(get_auth_client)]) -> AccessGateway:
    return AuthAccessGateway(client, retry=AUTH_BACKOFF, breaker=AUTH_BREAKER)


StorageDep = Annotated[DocumentStorage, Depends(get_storage)]
CacheDep = Annotated[ModelCache, Depends(get_cache)]
AccessGatewayDep = Annotated[AccessGateway, Depends(get_access_gateway)]


def get_access_service(gateway: AccessGatewayDep) -> AccessService:
    return AccessService(gateway)


AccessServiceDep = Annotated[AccessService, Depends(get_access_service)]


async def get_access(token: TokenDep, access_service: AccessServiceDep) -> Access:
    """Уровни доступа пользователя этого запроса."""
    return await access_service.for_token(token)


def get_film_service(storage: StorageDep, cache: CacheDep) -> FilmService:
    return FilmService(storage, cache)


def get_genre_service(storage: StorageDep, cache: CacheDep) -> GenreService:
    return GenreService(storage, cache)


def get_person_service(storage: StorageDep, cache: CacheDep) -> PersonService:
    return PersonService(storage, cache)


AccessDep = Annotated[Access, Depends(get_access)]
FilmServiceDep = Annotated[FilmService, Depends(get_film_service)]
GenreServiceDep = Annotated[GenreService, Depends(get_genre_service)]
PersonServiceDep = Annotated[PersonService, Depends(get_person_service)]
