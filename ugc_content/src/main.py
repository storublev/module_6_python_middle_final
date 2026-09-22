"""Сборка приложения FastAPI.

Каркас девятого спринта. Соединение с MongoDB открывается в `lifespan`, а не
на уровне модуля: импортировать приложение (в тестах, в скриптах) не должно
значить «сходить в базу». Там же инициализируется Beanie — ODM получает список
моделей документов и привязывает их к базе.

Драйвер — `pymongo.AsyncMongoClient`, а не `motor` из урока: с версии 2.0
Beanie работает через асинхронный клиент самого pymongo, motor объявлен
устаревшим.

Чего здесь пока нет и что появится вместе с реализацией (эпик E3 в
docs/planning-sprint9.md): маршруты `/content/api/v1/...`, обработчики ошибок,
проверка access-токена, требование заголовка `X-Request-Id` и подключение
Sentry.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.config import Settings, settings

API_PREFIX = '/content/api/v1'
DOCS_PREFIX = '/content/api'


def create_app(config: Settings | None = None) -> FastAPI:
    """Собирает приложение. `config` подменяется в тестах."""
    config = config or settings

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # TODO(E3): открыть AsyncMongoClient по config.mongo_uri,
        # инициализировать Beanie списком моделей документов, закрыть клиент
        # при остановке.
        yield

    app = FastAPI(
        title='UGC content service',
        description='Лайки, рецензии и закладки онлайн-кинотеатра',
        version='0.1.0',
        docs_url=f'{DOCS_PREFIX}/openapi',
        openapi_url=f'{DOCS_PREFIX}/openapi.json',
        lifespan=lifespan,
    )
    return app
