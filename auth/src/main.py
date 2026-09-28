import asyncio
import logging.config
import random
from contextlib import asynccontextmanager, suppress
from typing import cast

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.types import ExceptionHandler

from api.dependencies import get_access_cache, get_access_invalidator, get_invalidation_queue
from api.errors import (
    SERVICE_UNAVAILABLE_RESPONSE,
    service_error_handler,
    storage_unavailable_handler,
    validation_error_handler,
)
from api.v1 import access, auth, directory, oauth, profile, roles
from core.config import settings
from core.logger import LOGGING
from core.middleware import RequestIdMiddleware
from core.sentry import configure_sentry
from core.tracing import configure_tracing
from db import postgres, redis
from services.errors import ServiceError
from storage.base import StorageUnavailableError

logging.config.dictConfig(LOGGING)
configure_sentry(settings.sentry_dsn, settings.project_name, settings.sentry_environment)
logger = logging.getLogger(__name__)

API_PREFIX = '/auth/api/v1'


async def retry_access_invalidations() -> None:
    """Повторяет сброс кеша прав по заданиям, которые не удалось выполнить сразу.

    Работает в каждом процессе сервиса; одно задание два процесса не возьмут —
    задания блокируются в PostgreSQL. При сбое хранилища пауза удваивается до
    AUTH_ACCESS_INVALIDATION_MAX_INTERVAL, чтобы не долбить упавший Redis.
    """
    interval = settings.access_invalidation_interval.total_seconds()
    delay = interval
    while True:
        # Случайная добавка разводит процессы, запущенные одновременно; это не криптография.
        await asyncio.sleep(delay * random.uniform(0.8, 1.2))  # noqa: S311
        try:
            if postgres.session_factory is None or redis.redis is None:
                raise RuntimeError('Соединения с хранилищами не созданы')
            async with postgres.session_factory() as session:
                invalidator = get_access_invalidator(
                    get_invalidation_queue(session), get_access_cache(redis.redis),
                )
                if done := await invalidator.flush():
                    logger.info('Кеш прав сброшен по отложенным заданиям: %d', done)
        except StorageUnavailableError as exc:
            delay = min(delay * 2, settings.access_invalidation_max_interval.total_seconds())
            logger.warning('Сброс кеша прав не удался, следующая попытка через %.0f с: %s', delay, exc)
        except Exception:
            # Задача не должна умереть от неожиданной ошибки: задания дождутся исправления.
            logger.exception('Ошибка при сбросе кеша прав по отложенным заданиям')
        else:
            delay = interval


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
    invalidations = asyncio.create_task(retry_access_invalidations())
    yield
    invalidations.cancel()
    with suppress(asyncio.CancelledError):
        await invalidations
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
    {'name': 'oauth', 'description': 'Вход через соцсети: сторона потребителя OAuth 2.0.'},
    {
        'name': 'directory',
        'description': 'Служебный справочник контактов для сервиса уведомлений. '
                       'Доступ по заголовку `X-Service-Token`; пока секрет не задан, доступа нет ни у кого.',
    },
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
# Документация и её спецификация вызываются мимо nginx — в том числе проверкой
# живости контейнера, — поэтому идентификатор запроса с них не спрашивается и
# деревьев спанов они не порождают.
# Оба адреса заданы при создании приложения, но в типах FastAPI они
# объявлены как str | None — отсюда явный отбор непустых.
DOCS_PATHS = frozenset(path for path in (app.docs_url, app.openapi_url) if path)

configure_tracing(
    app,
    service_name=settings.project_name,
    endpoint=settings.otlp_endpoint,
    excluded_urls=','.join(DOCS_PATHS),
)
# Middleware добавляется после инструментирования: в ASGI обработчики
# оборачивают друг друга, и добавленный последним отрабатывает первым — так
# идентификатор попадает в спан, который создал инструментатор.
app.add_middleware(
    RequestIdMiddleware, required=settings.require_request_id, exempt_paths=DOCS_PATHS,
)

# Обработчики принимают конкретный тип исключения, а Starlette описывает их
# как принимающие Exception. Приведение — в одном месте, чтобы сами
# обработчики оставались честно типизированными.
for exception_type, handler in (
    (ServiceError, service_error_handler),
    (StorageUnavailableError, storage_unavailable_handler),
    (RequestValidationError, validation_error_handler),
):
    app.add_exception_handler(exception_type, cast(ExceptionHandler, handler))

app.include_router(auth.router, prefix=API_PREFIX, tags=['auth'], responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(profile.router, prefix=f'{API_PREFIX}/users', tags=['profile'],
                   responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(roles.router, prefix=f'{API_PREFIX}/roles', tags=['roles'], responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(access.router, prefix=API_PREFIX, tags=['access'], responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(oauth.router, prefix=API_PREFIX, tags=['oauth'], responses=SERVICE_UNAVAILABLE_RESPONSE)
# Справочник подключается всегда, а не только при заданном секрете: иначе
# спецификация OpenAPI зависела бы от настроек стенда и расходилась бы с
# файлом в репозитории. Без секрета в него просто нельзя попасть — пустое
# ожидаемое значение не совпадает ни с одним присланным.
app.include_router(directory.router, prefix=API_PREFIX, tags=['directory'], responses=SERVICE_UNAVAILABLE_RESPONSE)


if __name__ == '__main__':
    # Локальный запуск для разработки; в Docker сервис запускает uvicorn из CMD.
    uvicorn.run('main:app', host='127.0.0.1', port=8000, log_config=LOGGING, reload=True)
