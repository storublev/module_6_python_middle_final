import logging.config
from contextlib import asynccontextmanager
from http import HTTPStatus

import uvicorn
from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import ConnectionError as RedisConnectionError

from api.v1 import films, genres, persons
from api.v1.schemas import error_response
from core.config import settings
from core.logger import LOGGING
from core.middleware import TrailingSlashMiddleware
from db import elastic, redis
from storage.base import StorageUnavailableError

logging.config.dictConfig(LOGGING)

SERVICE_UNAVAILABLE = 'service temporarily unavailable'


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Кеш не должен задерживать ответ: таймауты короткие, а повторяется только
    # обрыв соединения (например, после перезапуска Redis) — несколько раз,
    # с короткой экспоненциальной паузой. Таймаут не повторяется: Redis, который
    # не ответил за секунду, вряд ли ответит на вторую попытку, и запрос уходит
    # в Elasticsearch. По умолчанию redis-py повторяет и таймауты, до 10 раз.
    redis.redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        socket_connect_timeout=1,
        socket_timeout=1,
        retry=Retry(
            ExponentialWithJitterBackoff(base=settings.redis_backoff_base, cap=settings.redis_backoff_cap),
            retries=settings.redis_backoff_retries,
            supported_errors=(RedisConnectionError,),
        ),
    )
    # Повторы с экспоненциальной паузой делает ElasticStorage; встроенные
    # повторы клиента идут без паузы и умножали бы число попыток.
    elastic.es = AsyncElasticsearch(
        hosts=[settings.elastic_url],
        request_timeout=settings.elastic_request_timeout,
        max_retries=0,
    )
    yield
    await redis.redis.aclose()
    await elastic.es.close()


API_DESCRIPTION = """
Информация о фильмах, жанрах и людях, участвовавших в создании произведения.
Все пользователи анонимные, авторизация не нужна.

**Списки** постраничные: `page_number` — номер страницы с 1, `page_size` —
от 1 до 100 элементов (по умолчанию 50). Произведение `page_number * page_size`
не больше 10 000, иначе ответ 422. Поиск сортируется по релевантности.

**Ошибки** возвращаются в поле `detail`:

* 404 — фильма, жанра или персоны с таким `uuid` нет;
* 422 — параметры запроса не прошли проверку: невалидный `uuid`, неизвестная
  сортировка, пустая строка поиска, страница за пределами выдачи;
* 503 — хранилище временно не может ответить, запрос стоит повторить позже.

**Кеш.** Ответы кешируются на {cache_expire} с: изменения в каталоге появляются
в API с этой задержкой.
""".format(cache_expire=settings.cache_expire_in_seconds)

OPENAPI_TAGS = [
    {'name': 'films', 'description': 'Фильмы: популярные, похожие, поиск и полная информация.'},
    {'name': 'genres', 'description': 'Жанры фильмов.'},
    {'name': 'persons', 'description': 'Актёры, сценаристы и режиссёры: поиск, данные и фильмы.'},
]

app = FastAPI(
    title=settings.project_name,
    summary='Async API онлайн-кинотеатра',
    description=API_DESCRIPTION,
    version='1.0.0',
    openapi_tags=OPENAPI_TAGS,
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
storage_responses = {HTTPStatus.SERVICE_UNAVAILABLE: error_response(SERVICE_UNAVAILABLE)}
app.include_router(films.router, prefix='/api/v1/films', tags=['films'], responses=storage_responses)
app.include_router(genres.router, prefix='/api/v1/genres', tags=['genres'], responses=storage_responses)
app.include_router(persons.router, prefix='/api/v1/persons', tags=['persons'], responses=storage_responses)


if __name__ == '__main__':
    uvicorn.run('main:app', host='0.0.0.0', port=8000, log_config=LOGGING, reload=True)
