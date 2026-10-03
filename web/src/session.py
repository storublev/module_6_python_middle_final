"""Сессия зрителя в интерфейсе: токены сервиса авторизации в cookie.

Почему cookie, а не localStorage. Токены лежат в cookie с `HttpOnly` — их не
прочитает ни скрипт страницы, ни внедрённый скрипт (XSS), — и с
`SameSite=Lax`: форма с чужого сайта не отправит их POST-запросом (CSRF).
Браузер отдаёт их только интерфейсу, а дальше интерфейс сам подставляет
access-токен в вызовы API (ADR-24).

Access-токен живёт 15 минут. Истёк — интерфейс сам меняет refresh-токен на
новую пару и кладёт её в cookie ответа: зритель не замечает, что его токен
обновился. Не удалось — сессия закрывается, и зритель видит сайт как гость.
"""

import logging
from dataclasses import dataclass
from urllib.parse import quote, unquote
from uuid import UUID

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from clients.api import AuthClient
from clients.base import ApiError, BackendUnavailableError
from core.config import settings

logger = logging.getLogger(__name__)

ACCESS_COOKIE = 'practix_access'
REFRESH_COOKIE = 'practix_refresh'
NAME_COOKIE = 'practix_name'
# refresh-токен живёт 14 дней — столько же живёт и cookie.
REFRESH_MAX_AGE = 14 * 24 * 3600
REQUIRED_CLAIMS = ['sub', 'sid', 'type', 'exp']
UNSAFE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})


@dataclass(frozen=True)
class Viewer:
    """Кто смотрит страницу: из проверенного access-токена."""

    user_id: UUID
    name: str
    token: str


@dataclass(frozen=True)
class Tokens:
    access: str
    refresh: str


def read_token(token: str) -> UUID | None:
    """user_id из действующего access-токена или None, если токен истёк или подделан.

    Raises:
        jwt.ExpiredSignatureError: срок истёк — пора обновить по refresh-токену.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key.get_secret_value(), algorithms=[settings.jwt_algorithm],
            options={'require': REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError:
        raise
    except jwt.InvalidTokenError:
        return None
    if payload.get('type') != 'access':
        return None
    try:
        return UUID(payload['sub'])
    except (TypeError, ValueError):
        return None


class SessionMiddleware(BaseHTTPMiddleware):
    """Опознаёт зрителя по cookie, обновляет истёкший токен и отсекает чужие POST."""

    def __init__(self, app, auth: AuthClient) -> None:  # noqa: ANN001 — ASGI-приложение
        super().__init__(app)
        self._auth = auth

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith('/static/'):
            return await call_next(request)
        if request.method in UNSAFE_METHODS and not same_origin(request):
            # SameSite=Lax уже не пустит cookie с чужого сайта, проверка
            # Origin — второй рубеж для браузеров, где Lax не по умолчанию.
            return PlainTextResponse('Cross-site request rejected', status_code=403)

        viewer, issued, expired = await self._viewer(request)
        request.state.viewer = viewer
        response = await call_next(request)
        if issued is not None:
            set_tokens(response, issued)
        elif expired:
            clear_session(response)
        return response

    async def _viewer(self, request: Request) -> tuple[Viewer | None, Tokens | None, bool]:
        """Зритель, новая пара токенов (если обновляли) и признак «сессию надо закрыть»."""
        access = request.cookies.get(ACCESS_COOKIE)
        refresh = request.cookies.get(REFRESH_COOKIE)
        name = unquote(request.cookies.get(NAME_COOKIE, '')) or 'Зритель'
        if access:
            try:
                user_id = read_token(access)
            except jwt.ExpiredSignatureError:
                user_id = None
            else:
                if user_id is not None:
                    return Viewer(user_id, name, access), None, False
        if not refresh:
            return None, None, bool(access)
        try:
            pair = await self._auth.refresh(refresh)
        except BackendUnavailableError as error:
            # Сервис авторизации прилёг: страница откроется гостю, а сессию
            # не трогаем — refresh-токен ещё пригодится, когда Auth вернётся.
            logger.warning('Не удалось обновить токен: %s', error)
            return None, None, False
        except ApiError:
            return None, None, True
        tokens = Tokens(pair['access_token'], pair['refresh_token'])
        try:
            user_id = read_token(tokens.access)
        except jwt.ExpiredSignatureError:
            user_id = None
        if user_id is None:
            return None, None, True
        return Viewer(user_id, name, tokens.access), tokens, False


def same_origin(request: Request) -> bool:
    origin = request.headers.get('origin')
    if not origin or origin == 'null':
        # Без Origin (старые браузеры, curl) решает SameSite у cookie.
        return origin is None
    host = request.headers.get('x-forwarded-host') or request.headers.get('host', '')
    return origin.split('://', 1)[-1] == host


def set_tokens(response: Response, tokens: Tokens) -> None:
    for name, value in ((ACCESS_COOKIE, tokens.access), (REFRESH_COOKIE, tokens.refresh)):
        response.set_cookie(
            name, value, max_age=REFRESH_MAX_AGE, httponly=True, samesite='lax', secure=settings.cookie_secure,
            path='/',
        )


def set_name(response: Response, name: str) -> None:
    # Имя — для шапки сайта, не секрет; но и скриптам страницы оно не нужно.
    # Кодируется: заголовки HTTP — latin-1, и кириллица в cookie без этого
    # не уйдёт в ответ вовсе.
    response.set_cookie(
        NAME_COOKIE, quote(name), max_age=REFRESH_MAX_AGE, httponly=True, samesite='lax',
        secure=settings.cookie_secure,
    )


def clear_session(response: Response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, NAME_COOKIE):
        response.delete_cookie(name, path='/')
