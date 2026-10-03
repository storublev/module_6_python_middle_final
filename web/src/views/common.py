"""Общее для страниц: шаблоны, клиенты API, зритель, переходы после форм."""

import re
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from clients.api import AuthClient, BookingClient, CatalogClient
from presentation import FILTERS, error_text, notice_text
from session import Viewer

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / 'templates'
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters.update(FILTERS)


class LoginRequired(Exception):  # noqa: N818 — это сигнал «перейти ко входу», а не ошибка
    """Страница только для вошедших: зритель уйдёт ко входу и вернётся обратно."""

    def __init__(self, next_url: str) -> None:
        super().__init__(next_url)
        self.next_url = next_url


def catalog(request: Request) -> CatalogClient:
    return request.app.state.catalog


def booking(request: Request) -> BookingClient:
    return request.app.state.booking


def auth(request: Request) -> AuthClient:
    return request.app.state.auth


def viewer(request: Request) -> Viewer | None:
    return getattr(request.state, 'viewer', None)


def required_viewer(request: Request) -> Viewer:
    current = viewer(request)
    if current is None:
        raise LoginRequired(str(request.url.path) + (f'?{request.url.query}' if request.url.query else ''))
    return current


Catalog = Annotated[CatalogClient, Depends(catalog)]
Booking = Annotated[BookingClient, Depends(booking)]
Auth = Annotated[AuthClient, Depends(auth)]
MaybeViewer = Annotated[Viewer | None, Depends(viewer)]
CurrentViewer = Annotated[Viewer, Depends(required_viewer)]


# Куда можно вернуть зрителя после формы или входа: только путь своего сайта.
# «//evil.example» браузер понял бы как адрес чужого сайта, поэтому второй
# слеш в начале и схема запрещены.
SAFE_PATH = re.compile(r'/(?![/\\])[\w/?=&%.\-]*')


def safe_path(value: str | None, default: str = '/') -> str:
    return value if value and len(value) <= 300 and SAFE_PATH.fullmatch(value) else default


def render(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    """Страница с общими для всех шаблонов данными: зритель, сообщение и ошибка из адреса."""
    return templates.TemplateResponse(
        request,
        name,
        {
            'viewer': viewer(request),
            'notice': notice_text(request.query_params.get('notice')),
            'error': error_text(request.query_params.get('error')),
            **context,
        },
        status_code=status_code,
    )


def back(path: str, **params: str) -> RedirectResponse:
    """Переход после формы (PRG): обновление страницы не повторит бронь."""
    query = urlencode({key: value for key, value in params.items() if value})
    if not query:
        return RedirectResponse(path, status_code=303)
    separator = '&' if '?' in path else '?'
    return RedirectResponse(f'{path}{separator}{query}', status_code=303)
