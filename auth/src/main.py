import logging.config
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.errors import (
    SERVICE_UNAVAILABLE_RESPONSE,
    service_error_handler,
    storage_unavailable_handler,
    validation_error_handler,
)
from api.v1 import access, auth, profile, roles
from core.config import settings
from core.logger import LOGGING
from db import postgres, redis
from services.errors import ServiceError
from storage.base import StorageUnavailableError

logging.config.dictConfig(LOGGING)

API_PREFIX = '/auth/api/v1'


@asynccontextmanager
async def lifespan(_: FastAPI):
    postgres.engine = postgres.create_engine(settings)
    postgres.session_factory = async_sessionmaker(postgres.engine, expire_on_commit=False)
    redis.redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        socket_connect_timeout=1,
        socket_timeout=1,
        # Повторяется только обрыв соединения; таймаут не повторяется: Redis,
        # не ответивший за секунду, вряд ли ответит на вторую попытку.
        retry=Retry(
            ExponentialWithJitterBackoff(base=settings.redis_backoff_base, cap=settings.redis_backoff_cap),
            retries=settings.redis_backoff_retries,
            supported_errors=(RedisConnectionError,),
        ),
    )
    yield
    await redis.redis.aclose()
    await postgres.engine.dispose()


API_DESCRIPTION = """
Регистрация, вход и управление доступом пользователей онлайн-кинотеатра.

**Токены.** Вход выдаёт пару JWT: короткоживущий access-токен (передаётся в
заголовке `Authorization: Bearer <token>`) и одноразовый refresh-токен для
получения новой пары. Каждый вход — отдельная сессия; выход закрывает
сессию, и её токены перестают действовать сразу, а не по истечении срока.

**Ошибки** возвращаются как `{"code": "...", "detail": "..."}`. По `code`
клиент решает, что делать дальше:

* 401 `token_expired` — срок access-токена истёк: обновите пару токенов;
* 401 `token_invalid` — токен повреждён, подделан (неверная подпись) или
  другого типа: войдите заново;
* 401 `token_revoked` — сессия закрыта (выход, смена пароля, повторное
  использование refresh-токена): войдите заново;
* 401 `not_authenticated` — токен не передан;
* 403 — токен в порядке, но не хватает прав или неверен текущий пароль;
* 404 — нет пользователя или роли, 409 — логин или имя роли заняты;
* 422 — запрос не прошёл проверку (формат FastAPI: список ошибок в `detail`);
* 429 `too_many_requests` — исчерпан лимит попыток входа или регистрации:
  повторите через `Retry-After` секунд;
* 503 `service_unavailable` — хранилище временно недоступно, повторите позже.

**Права.** Роль — набор прав вида `films.subscription`. Суперпользователю
разрешено всё, анонимному пользователю — только то, что правом не
ограничено. Управлять ролями может обладатель права `access.manage`.
"""

OPENAPI_TAGS = [
    {'name': 'auth', 'description': 'Регистрация, вход, обновление токенов и выход.'},
    {'name': 'profile', 'description': 'Личный кабинет: данные, смена логина и пароля, история входов.'},
    {'name': 'roles', 'description': 'Управление ролями. Нужно право `access.manage`.'},
    {'name': 'access', 'description': 'Назначение ролей пользователям и проверка прав.'},
]

app = FastAPI(
    title=settings.project_name,
    summary='Сервис авторизации онлайн-кинотеатра',
    description=API_DESCRIPTION,
    version='1.0.0',
    openapi_tags=OPENAPI_TAGS,
    docs_url='/auth/api/openapi',
    openapi_url='/auth/api/openapi.json',
    lifespan=lifespan,
)
app.add_exception_handler(ServiceError, service_error_handler)
app.add_exception_handler(StorageUnavailableError, storage_unavailable_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

app.include_router(auth.router, prefix=API_PREFIX, tags=['auth'], responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(profile.router, prefix=f'{API_PREFIX}/users', tags=['profile'],
                   responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(roles.router, prefix=f'{API_PREFIX}/roles', tags=['roles'], responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(access.router, prefix=API_PREFIX, tags=['access'], responses=SERVICE_UNAVAILABLE_RESPONSE)


if __name__ == '__main__':
    # Локальный запуск для разработки; в Docker сервис запускает uvicorn из CMD.
    uvicorn.run('main:app', host='127.0.0.1', port=8000, log_config=LOGGING, reload=True)
