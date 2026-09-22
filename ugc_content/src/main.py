"""Сборка приложения FastAPI.

Соединение с MongoDB открывается в `lifespan`, а не на уровне модуля:
импортировать приложение (в тестах, в скриптах) не должно значить «сходить в
базу». Там же инициализируется Beanie — ODM получает список моделей документов
и заводит индексы.

Драйвер — `pymongo.AsyncMongoClient`, а не `motor` из урока: с версии 2.0
Beanie работает через асинхронный клиент самого pymongo, motor объявлен
устаревшим.

Сервисы кладутся в `app.state`, а не собираются в зависимостях: так в
unit-тестах на их место встают хранилища в памяти, и приложение поднимается
без MongoDB вообще.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging.config import dictConfig

import uvicorn
from fastapi import FastAPI

from api.dependencies import Services, build_services
from api.errors import HANDLERS
from api.security import get_verifier
from api.v1 import bookmarks, health, likes, reviews
from core.config import Settings, settings
from core.logger import LOGGING
from core.middleware import RequestIdMiddleware
from core.sentry import configure_sentry
from core.tracing import configure_tracing
from services.content import BookmarkService, LikeService, ReviewService
from storage.mongo import (
    MongoBookmarkStorage,
    MongoHealthCheck,
    MongoLikeStorage,
    MongoReviewStorage,
    connect,
)

logger = logging.getLogger(__name__)

API_PREFIX = '/content/api/v1'
DOCS_PREFIX = '/content/api'

# Эти адреса вызываются мимо шлюза — healthcheck контейнера и браузер с
# документацией, — поэтому заголовка X-Request-Id от них не требуем.
EXEMPT_PATHS = frozenset({
    f'{API_PREFIX}/health',
    f'{API_PREFIX}/ready',
    f'{DOCS_PREFIX}/openapi',
    f'{DOCS_PREFIX}/openapi.json',
})


def create_app(config: Settings | None = None, services: Services | None = None) -> FastAPI:
    """Собирает приложение.

    `services` подменяются в тестах: с ними приложение работает на хранилищах
    в памяти и к MongoDB не подключается.
    """
    config = config or settings
    dictConfig(LOGGING)
    configure_sentry(config.sentry_dsn, config.project_name, config.sentry_environment)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        client = None
        if services is None:
            client = await connect(
                config.mongo_uri,
                config.mongo_database,
                connectTimeoutMS=config.mongo_connect_timeout_ms,
                serverSelectionTimeoutMS=config.mongo_server_selection_timeout_ms,
                socketTimeoutMS=config.mongo_socket_timeout_ms,
            )
            application.state.services = build_services(
                likes=LikeService(MongoLikeStorage()),
                reviews=ReviewService(MongoReviewStorage()),
                bookmarks=BookmarkService(MongoBookmarkStorage()),
                health=MongoHealthCheck(client),
            )
            logger.info('Подключение к MongoDB открыто: база %s', config.mongo_database)
        yield
        if client is not None:
            await client.close()
            logger.info('Подключение к MongoDB закрыто')

    app = FastAPI(
        title='UGC content service',
        description='Лайки, рецензии и закладки онлайн-кинотеатра',
        version='1.0.0',
        docs_url=f'{DOCS_PREFIX}/openapi',
        openapi_url=f'{DOCS_PREFIX}/openapi.json',
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.verifier = get_verifier(config)
    if services is not None:
        app.state.services = services

    for exception, handler in HANDLERS.items():
        app.add_exception_handler(exception, handler)
    app.add_middleware(RequestIdMiddleware, required=config.require_request_id, exempt_paths=EXEMPT_PATHS)
    configure_tracing(app, config.project_name, config.otlp_endpoint, excluded_urls=','.join(EXEMPT_PATHS))

    for router in (likes.router, reviews.router, bookmarks.router, health.router):
        app.include_router(router, prefix=API_PREFIX)
    return app


app = create_app()


if __name__ == '__main__':
    uvicorn.run('main:app', host='0.0.0.0', port=8000)  # noqa: S104 — в контейнере слушаем все интерфейсы
