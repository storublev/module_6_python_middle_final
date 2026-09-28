"""API сервиса уведомлений.

Центральный узел системы: принимает события и заявки на рассылку, ведёт
шаблоны и подписки, отдаёт зрителю его уведомления. **Рассылкой не
занимается** — за это отвечают воркеры (`worker.py`).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging.config import dictConfig
from typing import cast

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.types import ExceptionHandler

import db.postgres as postgres
from api.errors import (
    SERVICE_UNAVAILABLE_RESPONSE,
    service_error_handler,
    storage_unavailable_handler,
    validation_error_handler,
)
from api.security import get_verifier
from api.v1 import campaigns, events, me, public, templates
from core.config import settings
from core.logger import LOGGING
from core.middleware import RequestIdMiddleware
from core.sentry import configure_sentry
from core.tracing import configure_tracing
from services.errors import ServiceError
from services.renderer import Renderer
from storage.base import StorageUnavailableError
from storage.rabbit import RabbitPublisher, connect

dictConfig(LOGGING)
logger = logging.getLogger(__name__)
configure_sentry(settings.sentry_dsn, settings.project_name, settings.sentry_environment)

API_PREFIX = '/notify/api/v1'


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Соединения живут всё время работы приложения, а не создаются на запрос."""
    postgres.engine = postgres.create_engine(settings)
    postgres.session_factory = async_sessionmaker(postgres.engine, expire_on_commit=False)
    connection, channel = await connect(
        settings.rabbit_url.get_secret_value(),
        settings.rabbit_prefetch,
        int(settings.retry_delay.total_seconds() * 1000),
    )
    app.state.publisher = RabbitPublisher(channel)
    app.state.renderer = Renderer()
    app.state.verifier = get_verifier(settings)
    logger.info('Сервис уведомлений готов принимать события')
    try:
        yield
    finally:
        await connection.close()
        if postgres.engine is not None:
            await postgres.engine.dispose()


API_DESCRIPTION = """
Сервис уведомлений онлайн-кинотеатра.

**Как устроено.** Это центральный узел: он принимает события и кладёт их в
очередь, а собирают и отправляют письма воркеры. Поэтому ответ приходит сразу
и не зависит от того, сколько адресатов у события и жив ли почтовый сервер.

**Идемпотентность.** У события есть `event_id`, который задаёт отправитель.
Повтор запроса после потерянного ответа вернёт `accepted: false` и второго
письма не создаст.

**Кто ходит.** Эндпоинты событий, шаблонов и рассылок — для сервисов
кинотеатра и админ-панели, они опознаются заголовком `X-Service-Token`.
Личный кабинет (`/me/...`) — для зрителя с его access-токеном. Отписка и
подтверждение почты работают без входа: они нужны прямо из письма.

**Ошибки** отдаются в общем формате кинотеатра: `{"code": "...", "detail": "..."}`.
"""

OPENAPI_TAGS = [
    {'name': 'events', 'description': 'Приём событий от других сервисов кинотеатра.'},
    {'name': 'templates', 'description': 'Шаблоны писем: CRUD для админ-панели.'},
    {'name': 'campaigns', 'description': 'Рассылки менеджера: сразу, отложенно и повторяемо.'},
    {'name': 'me', 'description': 'Личный кабинет зрителя: свои уведомления и настройки подписок.'},
    {'name': 'public', 'description': 'Доступно из письма без входа: отписка и подтверждение адреса.'},
]

app = FastAPI(
    title=settings.project_name,
    summary='Сервис уведомлений онлайн-кинотеатра',
    description=API_DESCRIPTION,
    version='1.0.0',
    openapi_tags=OPENAPI_TAGS,
    docs_url='/notify/api/openapi',
    openapi_url='/notify/api/openapi.json',
    lifespan=lifespan,
)
# Эти адреса вызываются мимо шлюза — проверкой живости контейнера и браузером
# с документацией, — поэтому идентификатор запроса с них не спрашивается.
# Оба адреса документации заданы при создании приложения, но в типах FastAPI
# объявлены как str | None, отсюда явный отбор непустых.
DOCS_PATHS = frozenset(path for path in (app.docs_url, app.openapi_url) if path)
EXEMPT_PATHS = DOCS_PATHS | {f'{API_PREFIX}/health'}

configure_tracing(
    app,
    service_name=settings.project_name,
    endpoint=settings.otlp_endpoint,
    excluded_urls=','.join(DOCS_PATHS),
)
app.add_middleware(RequestIdMiddleware, required=settings.require_request_id, exempt_paths=EXEMPT_PATHS)

for exception_type, handler in (
    (ServiceError, service_error_handler),
    (StorageUnavailableError, storage_unavailable_handler),
    (RequestValidationError, validation_error_handler),
):
    app.add_exception_handler(exception_type, cast(ExceptionHandler, handler))

app.include_router(events.router, prefix=API_PREFIX, tags=['events'], responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(
    templates.router, prefix=f'{API_PREFIX}/templates', tags=['templates'], responses=SERVICE_UNAVAILABLE_RESPONSE,
)
app.include_router(
    campaigns.router, prefix=f'{API_PREFIX}/campaigns', tags=['campaigns'], responses=SERVICE_UNAVAILABLE_RESPONSE,
)
app.include_router(me.router, prefix=f'{API_PREFIX}/me', tags=['me'], responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(public.router, prefix=API_PREFIX, tags=['public'], responses=SERVICE_UNAVAILABLE_RESPONSE)
# Короткие ссылки живут в корне, а не под /notify/api/v1: в письме они должны
# быть короткими, ради этого всё и затевалось.
app.include_router(public.redirect_router, prefix='/s', tags=['public'])


if __name__ == '__main__':
    uvicorn.run('main:app', host='127.0.0.1', port=8000, log_config=LOGGING, reload=True)
