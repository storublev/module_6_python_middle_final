"""API сервиса бронирования билетов.

Хост предлагает фильм, место и время; гость выбирает хоста, дату и время и
бронирует места. Главная гарантия — **не продать больше мест, чем есть у
хоста** — держится на базе данных (ADR-21 в docs/diploma/architecture.md).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging.config import dictConfig
from typing import cast

import httpx
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
from api.v1 import bookings, me, public, screenings
from core.config import settings
from core.logger import LOGGING
from core.middleware import RequestIdMiddleware
from core.sentry import configure_sentry
from core.tracing import configure_tracing
from services.errors import ServiceError
from storage.base import StorageUnavailableError
from storage.http import HttpCatalog, HttpPeople, HttpSessions

dictConfig(LOGGING)
logger = logging.getLogger(__name__)
configure_sentry(settings.sentry_dsn, settings.project_name, settings.sentry_environment)

API_PREFIX = '/booking/api/v1'


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Соединения живут всё время работы приложения, а не создаются на запрос."""
    postgres.engine = postgres.create_engine(settings)
    postgres.session_factory = async_sessionmaker(postgres.engine, expire_on_commit=False)
    catalog_client = httpx.AsyncClient(base_url=settings.catalog_url, timeout=settings.catalog_timeout)
    auth_client = httpx.AsyncClient(base_url=settings.auth_url, timeout=settings.auth_timeout)
    app.state.catalog = HttpCatalog(catalog_client)
    app.state.people = HttpPeople(auth_client, settings.service_token.get_secret_value())
    # Свой прерыватель: справочник имён и проверка сессий отказывают по-разному
    # (имя заменяется подписью «Зритель», а запись без проверки сессии — нет).
    app.state.sessions = HttpSessions(auth_client)
    app.state.verifier = get_verifier(settings)
    logger.info('Сервис бронирования готов')
    try:
        yield
    finally:
        await catalog_client.aclose()
        await auth_client.aclose()
        if postgres.engine is not None:
            await postgres.engine.dispose()


API_DESCRIPTION = """
Бронирование билетов на совместные просмотры.

**Сценарий.** Хост создаёт показ: фильм из каталога (только полнометражный),
дату и время, место сбора и число мест. Гость в карточке фильма выбирает
хоста (`GET /films/{film_id}/hosts`), затем дату и время
(`GET /screenings?film_id=…&host_id=…`) и бронирует места
(`POST /screenings/{id}/bookings`).

**Гарантия.** Больше мест, чем есть у хоста, забронировать нельзя — ни одним
запросом, ни одновременными запросами разных гостей.

**Оценки.** После показа гость оценивает хоста, а хост — гостей; рейтинг
виден при выборе хоста.

**Вход.** Читать показы и рейтинги можно без входа; всё остальное — с
access-токеном сервиса авторизации (`POST /auth/api/v1/login`). Перед любой
записью сессия токена сверяется с сервисом авторизации: после выхода или смены
пароля старый токен изменить ничего не может (401 `token_revoked`).

**Ошибки** — в общем формате кинотеатра: `{"code": "...", "detail": "..."}`.
"""

OPENAPI_TAGS = [
    {'name': 'screenings', 'description': 'Показы: создание, поиск, изменение, отмена, брони и оценки показа.'},
    {'name': 'bookings', 'description': 'Брони гостя: изменение числа мест и отмена.'},
    {'name': 'me', 'description': 'Кабинет: расписание хоста и брони гостя.'},
    {'name': 'public', 'description': 'Хосты фильма и рейтинги зрителей — без входа.'},
]

app = FastAPI(
    title=settings.project_name,
    summary='Бронирование билетов на совместные просмотры',
    description=API_DESCRIPTION,
    version='1.0.0',
    openapi_tags=OPENAPI_TAGS,
    docs_url='/booking/api/openapi',
    openapi_url='/booking/api/openapi.json',
    lifespan=lifespan,
)
# Эти адреса вызываются мимо шлюза — проверкой живости контейнера и браузером
# с документацией, — поэтому идентификатор запроса с них не спрашивается.
DOCS_PATHS = frozenset(path for path in (app.docs_url, app.openapi_url) if path)
EXEMPT_PATHS = DOCS_PATHS | {f'{API_PREFIX}/health'}

configure_tracing(
    app,
    service_name=settings.project_name,
    endpoint=settings.otlp_endpoint,
    excluded_urls=','.join(EXEMPT_PATHS),
    sample_ratio=settings.otlp_sample_ratio,
)
app.add_middleware(RequestIdMiddleware, required=settings.require_request_id, exempt_paths=EXEMPT_PATHS)

for exception_type, handler in (
    (ServiceError, service_error_handler),
    (StorageUnavailableError, storage_unavailable_handler),
    (RequestValidationError, validation_error_handler),
):
    app.add_exception_handler(exception_type, cast(ExceptionHandler, handler))


@app.get(f'{API_PREFIX}/health', include_in_schema=False)
async def health() -> dict[str, str]:
    """Живость процесса для healthcheck контейнера: без базы и соседей."""
    return {'status': 'ok'}


app.include_router(
    screenings.router, prefix=f'{API_PREFIX}/screenings', tags=['screenings'], responses=SERVICE_UNAVAILABLE_RESPONSE,
)
app.include_router(
    bookings.router, prefix=f'{API_PREFIX}/bookings', tags=['bookings'], responses=SERVICE_UNAVAILABLE_RESPONSE,
)
app.include_router(me.router, prefix=f'{API_PREFIX}/me', tags=['me'], responses=SERVICE_UNAVAILABLE_RESPONSE)
app.include_router(public.router, prefix=API_PREFIX, tags=['public'], responses=SERVICE_UNAVAILABLE_RESPONSE)


if __name__ == '__main__':
    uvicorn.run('main:app', host='127.0.0.1', port=8000, log_config=LOGGING, reload=True)
