import logging.config
from contextlib import asynccontextmanager
from http import HTTPStatus

import uvicorn
from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from api.v1 import films, genres, persons
from core.config import settings
from core.logger import LOGGING
from core.middleware import TrailingSlashMiddleware
from db import elastic, redis
from storage.base import StorageUnavailableError

logging.config.dictConfig(LOGGING)

SERVICE_UNAVAILABLE = 'service temporarily unavailable'


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Короткие таймауты и без повторов: при недоступном Redis запрос должен
    # быстро уйти в Elasticsearch, а не ждать кеш. По умолчанию redis-py
    # повторяет операцию до 10 раз с паузами, и запрос висит секундами.
    redis.redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        socket_connect_timeout=1,
        socket_timeout=1,
        retry=Retry(NoBackoff(), retries=0),
    )
    elastic.es = AsyncElasticsearch(hosts=[settings.elastic_url])
    yield
    await redis.redis.aclose()
    await elastic.es.close()


app = FastAPI(
    title=settings.project_name,
    description='Информация о фильмах, жанрах и людях, участвовавших в создании произведения',
    version='1.0.0',
    docs_url='/api/openapi',
    openapi_url='/api/openapi.json',
    lifespan=lifespan,
)
app.add_middleware(TrailingSlashMiddleware)


@app.exception_handler(StorageUnavailableError)
async def storage_unavailable_handler(_: Request, __: StorageUnavailableError) -> JSONResponse:
    # Причина уже записана в журнал сервисом; клиенту — без внутренних подробностей.
    return JSONResponse(status_code=HTTPStatus.SERVICE_UNAVAILABLE, content={'detail': SERVICE_UNAVAILABLE})


# 503 возможен у любого эндпоинта, поэтому описан для роутеров целиком.
storage_responses = {HTTPStatus.SERVICE_UNAVAILABLE: {'description': SERVICE_UNAVAILABLE}}
app.include_router(films.router, prefix='/api/v1/films', tags=['films'], responses=storage_responses)
app.include_router(genres.router, prefix='/api/v1/genres', tags=['genres'], responses=storage_responses)
app.include_router(persons.router, prefix='/api/v1/persons', tags=['persons'], responses=storage_responses)


if __name__ == '__main__':
    uvicorn.run('main:app', host='0.0.0.0', port=8000, log_config=LOGGING, reload=True)
