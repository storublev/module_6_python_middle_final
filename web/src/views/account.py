"""Вход, регистрация, выход и кабинет: мои брони и моё расписание."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from clients.base import ApiError, BackendUnavailableError
from session import Tokens, clear_session, set_name, set_tokens
from views.common import Auth, Booking, CurrentViewer, MaybeViewer, back, render, safe_path

logger = logging.getLogger(__name__)
router = APIRouter()

Period = Literal['upcoming', 'past']


@router.get('/login', response_class=HTMLResponse, summary='Форма входа')
async def login_form(
    request: Request, next_url: Annotated[str, Query(alias='next', max_length=300)] = '/',
) -> HTMLResponse:
    return render(request, 'login.html', next_url=safe_path(next_url))


@router.post('/login', summary='Войти')
async def login(
    auth: Auth,
    login: Annotated[str, Form(max_length=150)],
    password: Annotated[str, Form(max_length=128)],
    next_url: Annotated[str, Form(max_length=300)] = '/',
) -> RedirectResponse:
    next_url = safe_path(next_url)
    try:
        pair = await auth.login(login.strip(), password)
    except (ApiError, BackendUnavailableError) as error:
        code = error.code if isinstance(error, ApiError) else 'unavailable'
        return back('/login', next=next_url, error=code)
    return await _signed_in(auth, pair, next_url)


@router.get('/register', response_class=HTMLResponse, summary='Форма регистрации')
async def register_form(request: Request) -> HTMLResponse:
    return render(request, 'register.html')


@router.post('/register', summary='Зарегистрироваться')
async def register(
    auth: Auth,
    login: Annotated[str, Form(max_length=150)],
    password: Annotated[str, Form(max_length=128)],
    first_name: Annotated[str, Form(max_length=64)] = '',
    email: Annotated[str, Form(max_length=254)] = '',
) -> RedirectResponse:
    try:
        await auth.signup(login.strip(), password)
        pair = await auth.login(login.strip(), password)
    except (ApiError, BackendUnavailableError) as error:
        code = error.code if isinstance(error, ApiError) else 'unavailable'
        return back('/register', error=code)
    # Имя и почта — для хоста и писем о бронях. Не сохранились — не беда:
    # их можно задать позже, а вход уже состоялся.
    profile = {key: value.strip() for key, value in (('first_name', first_name), ('email', email)) if value.strip()}
    if profile:
        try:
            await auth.update_profile(pair['access_token'], profile)
        except (ApiError, BackendUnavailableError) as error:
            logger.warning('Профиль при регистрации не сохранён: %s', error)
    return await _signed_in(auth, pair, '/?notice=welcome')


async def _signed_in(auth: Auth, pair: dict, next_url: str) -> RedirectResponse:
    name = 'Зритель'
    try:
        me = await auth.me(pair['access_token'])
        name = ' '.join(filter(None, (me.get('first_name'), me.get('last_name')))) or me.get('login') or name
    except (ApiError, BackendUnavailableError) as error:
        logger.warning('Имя после входа не получено: %s', error)
    response = RedirectResponse(next_url, status_code=303)
    set_tokens(response, Tokens(pair['access_token'], pair['refresh_token']))
    set_name(response, name)
    return response


@router.post('/logout', summary='Выйти')
async def logout(auth: Auth, viewer: MaybeViewer) -> RedirectResponse:
    if viewer is not None:
        try:
            await auth.logout(viewer.token)
        except (ApiError, BackendUnavailableError) as error:
            # Сессию в Auth не закрыли — cookie всё равно удаляем: зритель
            # просил выйти, и на этом устройстве он выйдет.
            logger.warning('Сессия в сервисе авторизации не закрыта: %s', error)
    response = RedirectResponse('/', status_code=303)
    clear_session(response)
    return response


@router.get('/me/bookings', response_class=HTMLResponse, summary='Мои брони')
async def my_bookings(
    request: Request, booking: Booking, viewer: CurrentViewer, period: Period = 'upcoming',
) -> HTMLResponse:
    try:
        page = await booking.my_bookings(viewer.token, period)
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    return render(request, 'my_bookings.html', page=page, period=period)


@router.get('/me/screenings', response_class=HTMLResponse, summary='Моё расписание')
async def my_screenings(
    request: Request, booking: Booking, viewer: CurrentViewer, period: Period = 'upcoming',
) -> HTMLResponse:
    try:
        page = await booking.my_screenings(viewer.token, period)
    except BackendUnavailableError:
        return render(request, 'unavailable.html', status_code=503)
    return render(request, 'my_screenings.html', page=page, period=period)
