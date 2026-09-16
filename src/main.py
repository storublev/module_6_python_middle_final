import logging.config
from contextlib import asynccontextmanager
from http import HTTPStatus

import httpx
import uvicorn
from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import ConnectionError as RedisConnectionError

from api.v1 import films, genres, persons
from api.v1.films import SUBSCRIPTION_REQUIRED, SUBSCRIPTION_UNVERIFIABLE
from api.v1.schemas import error_response
from core.config import settings
from core.logger import LOGGING
from core.middleware import RequestIdMiddleware, TrailingSlashMiddleware
from core.tracing import configure_tracing
from db import auth, elastic, redis
from services.errors import AccessCheckUnavailableError, SubscriptionRequiredError
from storage.access import TokenRejectedError
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
    # Пул постоянных соединений с сервисом авторизации: на каждую проверку
    # прав не тратим рукопожатие TCP. Повторы и прерыватель — в AuthAccessGateway.
    auth.client = httpx.AsyncClient(
        base_url=settings.auth_api_url,
        timeout=httpx.Timeout(settings.auth_request_timeout, connect=settings.auth_connect_timeout),
    )
    yield
    await auth.client.aclose()
    await redis.redis.aclose()
    await elastic.es.close()


API_DESCRIPTION = f"""
Информация о фильмах, жанрах и людях, участвовавших в создании произведения.

**Доступ.** Жанры и персоны открыты всем. Фильмы делятся на публичные и
доступные по подписке: фильм, вышедший менее трёх лет назад, виден только
пользователю с правом `films.subscription`. Токен передаётся заголовком
`Authorization: Bearer <access-токен>` из `POST /auth/api/v1/login`; без него
запрос считается анонимным, и доступны только публичные фильмы. Списки и
поиск недоступные фильмы не показывают, карточка такого фильма отвечает 403.

**Списки** постраничные: `page_number` — номер страницы с 1, `page_size` —
от 1 до 100 элементов (по умолчанию 50). Произведение `page_number * page_size`
не больше 10 000, иначе ответ 422. Поиск сортируется по релевантности.

**Ошибки** возвращаются в поле `detail`:

* 401 — сервис авторизации не принял токен: он истёк, повреждён или его сессия
  закрыта. Обновите пару токенов или войдите заново;
* 403 — фильм доступен только по подписке, а у пользователя её нет;
* 404 — фильма, жанра или персоны с таким `uuid` нет;
* 422 — параметры запроса не прошли проверку: невалидный `uuid`, неизвестная
  сортировка, пустая строка поиска, страница за пределами выдачи;
* 503 — хранилище или сервис авторизации временно не могут ответить, запрос
  стоит повторить позже.

**Если сервис авторизации недоступен**, каталог продолжает работать: списки и
поиск отдают публичные фильмы, как анонимному пользователю, а карточка
подписочного фильма отвечает 503 — подписку не подтвердить, но и отказывать
в ней неверно.

**Кеш.** Ответы кешируются на {settings.cache_expire_in_seconds} с: изменения
в каталоге появляются в API с этой задержкой. Права в кеш не попадают — они
спрашиваются у сервиса авторизации на каждый запрос с токеном, поэтому
отозванная подписка закрывает доступ сразу.
"""

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
# Документация вызывается мимо nginx, в том числе проверками готовности:
# идентификатор запроса с неё не спрашивается, спаны по ней не строятся.
DOCS_PATHS = frozenset({app.docs_url, app.openapi_url})

configure_tracing(
    app,
    service_name=settings.project_name,
    endpoint=settings.otlp_endpoint,
    excluded_urls=','.join(DOCS_PATHS),
)
# Порядок важен: добавленный последним отрабатывает первым. Косая черта в
# конце пути отбрасывается до всего остального, а идентификатор запроса
# попадает в спан, который создал инструментатор.
app.add_middleware(
    RequestIdMiddleware, required=settings.require_request_id, exempt_paths=DOCS_PATHS,
)
app.add_middleware(TrailingSlashMiddleware)


@app.exception_handler(StorageUnavailableError)
async def storage_unavailable_handler(_: Request, __: StorageUnavailableError) -> JSONResponse:
    # Причина уже записана в журнал сервисом; клиенту — без внутренних подробностей.
    return JSONResponse(status_code=HTTPStatus.SERVICE_UNAVAILABLE, content={'detail': SERVICE_UNAVAILABLE})


@app.exception_handler(SubscriptionRequiredError)
async def subscription_required_handler(_: Request, __: SubscriptionRequiredError) -> JSONResponse:
    return JSONResponse(status_code=HTTPStatus.FORBIDDEN, content={'detail': SUBSCRIPTION_REQUIRED})


@app.exception_handler(AccessCheckUnavailableError)
async def access_check_unavailable_handler(_: Request, __: AccessCheckUnavailableError) -> JSONResponse:
    # Подписка не подтверждена, но и не опровергнута: 503, а не 403, — клиенту
    # стоит повторить запрос, а не решать, что доступ закрыт.
    return JSONResponse(status_code=HTTPStatus.SERVICE_UNAVAILABLE, content={'detail': SUBSCRIPTION_UNVERIFIABLE})


@app.exception_handler(TokenRejectedError)
async def token_rejected_handler(_: Request, exc: TokenRejectedError) -> JSONResponse:
    # Причину отказа формулирует сервис авторизации — по ней клиент решает,
    # обновлять пару токенов или входить заново.
    return JSONResponse(
        status_code=HTTPStatus.UNAUTHORIZED,
        content={'detail': exc.detail},
        headers={'WWW-Authenticate': 'Bearer'},
    )


# 503 возможен у любого эндпоинта, поэтому описан для роутеров целиком.
storage_responses = {HTTPStatus.SERVICE_UNAVAILABLE: error_response(SERVICE_UNAVAILABLE)}
app.include_router(films.router, prefix='/api/v1/films', tags=['films'], responses=storage_responses)
app.include_router(genres.router, prefix='/api/v1/genres', tags=['genres'], responses=storage_responses)
app.include_router(persons.router, prefix='/api/v1/persons', tags=['persons'], responses=storage_responses)


if __name__ == '__main__':
    # Локальный запуск для разработки; в Docker сервис запускает uvicorn из CMD.
    uvicorn.run('main:app', host='127.0.0.1', port=8000, log_config=LOGGING, reload=True)
