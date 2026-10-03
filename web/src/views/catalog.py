"""Каталог, карточка фильма, персона, хост и афиша показов.

Страница собирается из нескольких API, и запросы идут **параллельно**
(`asyncio.gather`): иначе их задержки сложились бы и не уложились в 300 мс.
Отказ второстепенного источника не роняет страницу: карточка фильма без
блока брони лучше, чем ошибка 503 вместо карточки.
"""

import asyncio
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from clients.base import ApiError, BackendUnavailableError
from core.config import settings
from views.common import Booking, Catalog, MaybeViewer, render

logger = logging.getLogger(__name__)
router = APIRouter()

BOOKABLE_TYPE = 'movie'


async def optional[T](call: Any, default: T) -> T:
    """Ответ второстепенного источника или значение по умолчанию, если он молчит."""
    try:
        return await call
    except (BackendUnavailableError, ApiError) as error:
        logger.warning('Блок страницы пропущен: %s', error)
        return default


@router.get('/', response_class=HTMLResponse, summary='Каталог фильмов')
async def home(
    request: Request,
    catalog: Catalog,
    viewer: MaybeViewer,
    page: Annotated[int, Query(ge=1, le=400)] = 1,
    genre: Annotated[UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> HTMLResponse:
    query = (q or '').strip() or None
    token = viewer.token if viewer else None
    films: list[dict[str, Any]]
    genres: list[dict[str, Any]]
    try:
        films, genres = await asyncio.gather(
            catalog.films(page, settings.catalog_page_size, str(genre) if genre else None, query, token),
            optional(catalog.genres(), []),
        )
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    except ApiError:
        # Слишком далёкая страница или пустой запрос поиска — показываем
        # пустую выдачу, а не ошибку: это не сбой.
        films, genres = [], []
    return render(
        request, 'catalog.html',
        films=films, genres=genres, page=page, genre=str(genre) if genre else None, q=query,
        has_next=len(films) == settings.catalog_page_size,
    )


@router.get('/films/{film_id}', response_class=HTMLResponse, summary='Карточка фильма')
async def film_card(
    request: Request,
    film_id: UUID,
    catalog: Catalog,
    booking: Booking,
    viewer: MaybeViewer,
    host: Annotated[UUID | None, Query(description='Выбранный хост')] = None,
) -> HTMLResponse:
    token = viewer.token if viewer else None
    film_call = catalog.film(film_id, token)
    hosts_call = optional(booking.hosts(film_id), None)
    dates_call = optional(booking.screenings(film_id=film_id, host_id=host), None) if host else _none()
    try:
        film, hosts, dates = await asyncio.gather(film_call, hosts_call, dates_call)
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    except ApiError as error:
        if error.status == 403:
            return render(request, 'subscription.html', status_code=403)
        return render(request, 'not_found.html', status_code=404, what='Фильм')
    selected = None
    if host and hosts:
        selected = next((offer for offer in hosts['items'] if offer['host_id'] == str(host)), None)
    return render(
        request, 'film.html',
        film=film,
        bookable=film.get('type') == BOOKABLE_TYPE,
        hosts=hosts,
        selected=selected,
        dates=dates,
    )


async def _none() -> None:
    return None


@router.get('/persons/{person_id}', response_class=HTMLResponse, summary='Персона и её фильмы')
async def person(request: Request, person_id: UUID, catalog: Catalog, viewer: MaybeViewer) -> HTMLResponse:
    try:
        info, films = await asyncio.gather(
            catalog.person(person_id), catalog.person_films(person_id, viewer.token if viewer else None),
        )
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    except ApiError:
        return render(request, 'not_found.html', status_code=404, what='Персона')
    roles = {film['uuid']: film['roles'] for film in info.get('films', [])}
    return render(request, 'person.html', person=info, films=films, roles=roles)


@router.get('/hosts/{user_id}', response_class=HTMLResponse, summary='Страница хоста')
async def host_page(request: Request, user_id: UUID, booking: Booking) -> HTMLResponse:
    try:
        rating, reviews, screenings = await asyncio.gather(
            booking.rating(user_id), booking.reviews(user_id, 'host'), booking.screenings(host_id=user_id),
        )
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    if rating.get('name') is None and not screenings['items']:
        return render(request, 'not_found.html', status_code=404, what='Хост')
    return render(request, 'host.html', host=rating, reviews=reviews, screenings=screenings, host_id=str(user_id))


@router.get('/afisha', response_class=HTMLResponse, summary='Афиша совместных показов')
async def afisha(
    request: Request, booking: Booking, page: Annotated[int, Query(ge=1, le=1000)] = 1,
) -> HTMLResponse:
    try:
        screenings = await booking.screenings(page=page, size=24)
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    return render(request, 'afisha.html', screenings=screenings, page=page)
