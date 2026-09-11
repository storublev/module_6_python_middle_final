import logging.config
from contextlib import asynccontextmanager

import uvicorn
from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI
from redis.asyncio import Redis

from api.v1 import films, genres, persons
from core.config import settings
from core.logger import LOGGING
from core.middleware import TrailingSlashMiddleware
from db import elastic, redis

logging.config.dictConfig(LOGGING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Короткие таймауты: при недоступном Redis запрос должен быстро уйти
    # в Elasticsearch, а не ждать кеш.
    redis.redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        socket_connect_timeout=1,
        socket_timeout=1,
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

app.include_router(films.router, prefix='/api/v1/films', tags=['films'])
app.include_router(genres.router, prefix='/api/v1/genres', tags=['genres'])
app.include_router(persons.router, prefix='/api/v1/persons', tags=['persons'])


if __name__ == '__main__':
    uvicorn.run('main:app', host='0.0.0.0', port=8000, log_config=LOGGING, reload=True)
