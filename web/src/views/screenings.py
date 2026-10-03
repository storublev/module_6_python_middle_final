"""Показ: создать, посмотреть, изменить, отменить; забронировать места и оценить.

Все изменения — формы POST с переходом после (PRG): обновление страницы не
повторит бронь. Ошибка сервиса возвращает зрителя туда, откуда он пришёл, с
понятным текстом по коду ошибки.
"""

import asyncio
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from clients.base import ApiError, BackendUnavailableError
from presentation import ZONE
from views.common import Booking, Catalog, CurrentViewer, MaybeViewer, back, render, safe_path

router = APIRouter()

Text = Annotated[str, Form(max_length=2000)]


def code_of(error: Exception) -> str:
    return error.code if isinstance(error, ApiError) else 'unavailable'


def aware(local_value: str) -> str:
    """Время из <input type="datetime-local"> — это время зрителя; API ждёт его с поясом."""
    return datetime.fromisoformat(local_value).replace(tzinfo=ZONE).isoformat()


@router.get('/screenings/new', response_class=HTMLResponse, summary='Форма нового показа')
async def new_screening(
    request: Request, catalog: Catalog, viewer: CurrentViewer, film_id: Annotated[UUID, Query()],
) -> HTMLResponse:
    try:
        film = await catalog.film(film_id, viewer.token)
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    except ApiError:
        return render(request, 'not_found.html', status_code=404, what='Фильм')
    return render(request, 'screening_form.html', film=film, screening=None)


@router.post('/screenings', summary='Создать показ')
async def create_screening(
    booking: Booking,
    viewer: CurrentViewer,
    film_id: Annotated[UUID, Form()],
    starts_at: Annotated[str, Form()],
    place: Annotated[str, Form(max_length=255)],
    address: Annotated[str, Form(max_length=512)],
    capacity: Annotated[int, Form()],
    description: Text = '',
) -> RedirectResponse:
    try:
        body = {
            'film_id': str(film_id), 'starts_at': aware(starts_at), 'place': place, 'address': address,
            'capacity': capacity, 'description': description.strip() or None,
        }
        created = await booking.create(viewer.token, body)
    except ValueError:
        return back('/screenings/new', film_id=str(film_id), error='validation_error')
    except (ApiError, BackendUnavailableError) as error:
        return back('/screenings/new', film_id=str(film_id), error=code_of(error))
    return back(f'/screenings/{created["id"]}', notice='screening_created')


@router.get('/screenings/{screening_id}', response_class=HTMLResponse, summary='Страница показа')
async def screening_page(
    request: Request, screening_id: UUID, booking: Booking, viewer: MaybeViewer,
) -> HTMLResponse:
    try:
        screening = await booking.screening(screening_id)
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    except ApiError:
        return render(request, 'not_found.html', status_code=404, what='Показ')

    context: dict[str, Any] = {'screening': screening, 'is_host': False, 'mine': None, 'guests': [], 'rated': set()}
    started = datetime.fromisoformat(screening['starts_at']) <= datetime.now(ZONE)
    context['started'] = started
    if viewer is not None:
        context['is_host'] = screening['host_id'] == str(viewer.user_id)
        try:
            mine, guests, ratings = await asyncio.gather(
                _mine(booking, viewer.token, screening_id),
                booking.guests(viewer.token, screening_id) if context['is_host'] else _empty(),
                booking.my_ratings(viewer.token, screening_id) if started else _empty(),
            )
        except BackendUnavailableError:
            return render(request, 'unavailable.html', status_code=503)
        context |= {'mine': mine, 'guests': guests, 'rated': {rating['target_id'] for rating in ratings}}
    return render(request, 'screening.html', **context)


async def _empty() -> list[dict[str, Any]]:
    return []


async def _mine(booking: Any, token: str, screening_id: UUID) -> dict | None:
    try:
        return await booking.my_booking(token, screening_id)
    except ApiError:
        return None


@router.get('/screenings/{screening_id}/edit', summary='Форма правки показа')
async def edit_screening(
    request: Request, screening_id: UUID, booking: Booking, viewer: CurrentViewer,
) -> Response:
    try:
        screening = await booking.screening(screening_id)
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    except ApiError:
        return render(request, 'not_found.html', status_code=404, what='Показ')
    if screening['host_id'] != str(viewer.user_id):
        return back(f'/screenings/{screening_id}', error='not_screening_host')
    film = {'uuid': screening['film_id'], 'title': screening['film_title'], 'poster_url': screening['film_poster']}
    return render(request, 'screening_form.html', film=film, screening=screening)


@router.post('/screenings/{screening_id}/edit', summary='Изменить показ')
async def update_screening(
    screening_id: UUID,
    booking: Booking,
    viewer: CurrentViewer,
    starts_at: Annotated[str, Form()],
    place: Annotated[str, Form(max_length=255)],
    address: Annotated[str, Form(max_length=512)],
    capacity: Annotated[int, Form()],
    description: Text = '',
) -> RedirectResponse:
    try:
        current = await booking.screening(screening_id)
        changes = _changes(current, aware(starts_at), place, address, capacity, description.strip() or None)
        if changes:
            await booking.update(viewer.token, screening_id, changes)
    except ValueError:
        return back(f'/screenings/{screening_id}/edit', error='validation_error')
    except (ApiError, BackendUnavailableError) as error:
        return back(f'/screenings/{screening_id}/edit', error=code_of(error))
    return back(f'/screenings/{screening_id}', notice='screening_updated' if changes else '')


def _changes(current: dict, starts_at: str, place: str, address: str, capacity: int, description: str | None) -> dict:
    """Только изменённые поля: иначе гости получили бы письмо о «переносе» на то же время."""
    changes: dict[str, Any] = {}
    if datetime.fromisoformat(starts_at) != datetime.fromisoformat(current['starts_at']):
        changes['starts_at'] = starts_at
    for key, value in (('place', place), ('address', address), ('capacity', capacity)):
        if value != current[key]:
            changes[key] = value
    if description != current['description'] and description is not None:
        changes['description'] = description
    return changes


@router.post('/screenings/{screening_id}/cancel', summary='Отменить показ')
async def cancel_screening(screening_id: UUID, booking: Booking, viewer: CurrentViewer) -> RedirectResponse:
    try:
        await booking.cancel(viewer.token, screening_id)
    except (ApiError, BackendUnavailableError) as error:
        return back(f'/screenings/{screening_id}', error=code_of(error))
    return back(f'/screenings/{screening_id}', notice='screening_cancelled')


@router.post('/screenings/{screening_id}/book', summary='Забронировать места')
async def book(
    screening_id: UUID,
    booking: Booking,
    viewer: CurrentViewer,
    seats: Annotated[int, Form(ge=1, le=50)] = 1,
    back_to: Annotated[str, Form(max_length=300)] = '',
) -> RedirectResponse:
    try:
        await booking.book(viewer.token, screening_id, seats)
    except (ApiError, BackendUnavailableError) as error:
        return back(safe_path(back_to, f'/screenings/{screening_id}'), error=code_of(error))
    return back('/me/bookings', notice='booked')


@router.post('/bookings/{booking_id}/seats', summary='Изменить число мест')
async def change_seats(
    booking_id: UUID,
    booking: Booking,
    viewer: CurrentViewer,
    screening_id: Annotated[UUID, Form()],
    seats: Annotated[int, Form(ge=1, le=50)],
) -> RedirectResponse:
    try:
        await booking.change_seats(viewer.token, booking_id, seats)
    except (ApiError, BackendUnavailableError) as error:
        return back(f'/screenings/{screening_id}', error=code_of(error))
    return back(f'/screenings/{screening_id}', notice='seats_changed')


@router.post('/bookings/{booking_id}/cancel', summary='Отменить бронь')
async def cancel_booking(
    booking_id: UUID, booking: Booking, viewer: CurrentViewer, screening_id: Annotated[UUID, Form()],
) -> RedirectResponse:
    try:
        await booking.cancel_booking(viewer.token, booking_id)
    except (ApiError, BackendUnavailableError) as error:
        return back(f'/screenings/{screening_id}', error=code_of(error))
    return back(f'/screenings/{screening_id}', notice='booking_cancelled')


@router.post('/screenings/{screening_id}/rate', summary='Оценить участника показа')
async def rate(
    screening_id: UUID,
    booking: Booking,
    viewer: CurrentViewer,
    target_id: Annotated[UUID, Form()],
    score: Annotated[int, Form(ge=1, le=5)],
    comment: Annotated[str, Form(max_length=1000)] = '',
) -> RedirectResponse:
    try:
        await booking.rate(viewer.token, screening_id, target_id, score, comment.strip())
    except (ApiError, BackendUnavailableError) as error:
        return back(f'/screenings/{screening_id}', error=code_of(error))
    return back(f'/screenings/{screening_id}', notice='rated')
