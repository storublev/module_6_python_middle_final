"""Интерфейс онлайн-кинотеатра: каталог с обложками, карточка фильма и бронирование билетов.

Серверные страницы на FastAPI и Jinja2 — BFF (ADR-24): браузер ходит только
сюда, а сервис сам, параллельно, ходит во внутренние API каталога,
бронирования и авторизации.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http import HTTPStatus
from logging.config import dictConfig
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from clients.api import AuthClient, BookingClient, CatalogClient
from core.config import settings
from core.logger import LOGGING
from core.middleware import RequestIdMiddleware
from core.sentry import configure_sentry
from core.tracing import configure_tracing
from session import SessionMiddleware
from views import account, catalog, screenings
from views.common import LoginRequired, render

dictConfig(LOGGING)
logger = logging.getLogger(__name__)
configure_sentry(settings.sentry_dsn, settings.project_name, settings.sentry_environment)

STATIC_DIR = Path(__file__).resolve().parent / 'static'
HEALTH_PATH = '/health'


@dataclass(frozen=True)
class Backends:
    """Клиенты внутренних API — всё, что интерфейсу нужно снаружи."""

    catalog: CatalogClient
    booking: BookingClient
    auth: AuthClient

    @classmethod
    def from_settings(cls) -> tuple['Backends', list[httpx.AsyncClient]]:
        # Пул соединений на сервис: страница ходит в API на каждый запрос, и
        # открывать соединение заново — лишние миллисекунды из бюджета в 300 мс.
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        clients = [
            httpx.AsyncClient(base_url=url, timeout=settings.api_timeout, limits=limits)
            for url in (settings.catalog_url, settings.booking_url, settings.auth_url)
        ]
        return cls(CatalogClient(clients[0]), BookingClient(clients[1]), AuthClient(clients[2])), clients


def create_app(backends: Backends, owned: list[httpx.AsyncClient] | None = None) -> FastAPI:
    """Приложение поверх заданных клиентов API: в бою — настоящих, в тестах — подменённых."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info('Интерфейс готов')
        try:
            yield
        finally:
            for client in owned or []:
                await client.aclose()

    application = FastAPI(
        title='Practix', summary='Интерфейс онлайн-кинотеатра',
        docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
    )
    application.state.catalog = backends.catalog
    application.state.booking = backends.booking
    application.state.auth = backends.auth
    configure_tracing(
        application, service_name=settings.project_name, endpoint=settings.otlp_endpoint, excluded_urls='/static',
    )
    application.add_middleware(SessionMiddleware, auth=backends.auth)
    application.add_middleware(
        RequestIdMiddleware, required=settings.require_request_id, exempt_paths=frozenset({HEALTH_PATH}),
    )
    application.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
    application.include_router(catalog.router)
    application.include_router(screenings.router)
    application.include_router(account.router)
    application.add_api_route(HEALTH_PATH, health, include_in_schema=False)
    application.add_exception_handler(LoginRequired, login_required)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, invalid_form)  # type: ignore[arg-type]
    application.add_exception_handler(StarletteHTTPException, http_error)  # type: ignore[arg-type]
    return application


async def health() -> dict[str, str]:
    return {'status': 'ok'}


async def login_required(_: Request, exc: LoginRequired) -> RedirectResponse:
    return RedirectResponse('/login?' + urlencode({'next': exc.next_url, 'error': 'login_required'}), status_code=303)


async def invalid_form(request: Request, _: RequestValidationError) -> Response:
    """Неверная форма — назад, откуда пришли, с подсказкой; не JSON с ошибками FastAPI."""
    referer = urlsplit(request.headers.get('referer', ''))
    if request.method == 'POST' and referer.path.startswith('/') and referer.netloc == request.url.netloc:
        return RedirectResponse(f'{referer.path}?error=validation_error', status_code=303)
    return render(request, 'not_found.html', status_code=HTTPStatus.NOT_FOUND, what='Страница')


async def http_error(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
    if exc.status_code == HTTPStatus.NOT_FOUND:
        return render(request, 'not_found.html', status_code=HTTPStatus.NOT_FOUND, what='Страница')
    return HTMLResponse(str(exc.detail), status_code=exc.status_code)


app = create_app(*Backends.from_settings())

if __name__ == '__main__':
    uvicorn.run('main:app', host='127.0.0.1', port=8000, log_config=LOGGING, reload=True)
