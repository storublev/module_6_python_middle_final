"""Websocket-шлюз: мгновенные уведомления в открытую вкладку.

Отдельный процесс, а не часть API. Причина в уроке «Что-нибудь кроме почты?»:
websocket держит долгоживущее соединение, и каждое такое соединение занимает
место в цикле событий. Смешивать тысячи ожидающих соединений с обычными
запросами API нельзя — обслуживание каталога встанет.

**Авторизация обязательна**, об этом чек-лист задания говорит прямо: без
проверки токена к чужому потоку уведомлений подключился бы кто угодно. Токен
передаётся параметром запроса `?token=...`: браузерный WebSocket API не даёт
задать заголовки, и это единственный способ, который работает у всех клиентов.

Доставку инициирует не шлюз, а воркер-отправщик: он присылает служебный
HTTP-запрос `POST /internal/push`. Если зритель не в сети, шлюз честно
отвечает «некому» — сообщение уже записано в историю уведомлений и видно в
личном кабинете, повторять его бессмысленно.

Почему FastAPI, а не сервер библиотеки `websockets` из урока. У неё
`process_request` умеет отвечать на обычные запросы, но **не читает их тело**:
служебная доставка с телом JSON через неё не проходит — соединение рвётся до
того, как тело будет прочитано. Проверять пришлось бы вторым сервером на
втором порту. FastAPI умеет и websocket, и обычные запросы в одном приложении,
и это тот же стек, что у остальных сервисов кинотеатра.
"""

import logging
from collections import defaultdict
from http import HTTPStatus
from logging.config import dictConfig
from typing import Annotated, Any
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.security import get_verifier
from core.config import settings
from core.logger import LOGGING
from core.sentry import configure_sentry
from services.errors import ServiceError, ServiceTokenInvalidError

dictConfig(LOGGING)
logger = logging.getLogger(__name__)
configure_sentry(settings.sentry_dsn, f'{settings.project_name}-ws', settings.sentry_environment)

# Линтер принимает имя заголовка за пароль из-за слова token.
SERVICE_TOKEN_HEADER = 'X-Service-Token'  # noqa: S105
# Код закрытия соединения, когда токен не подошёл. 1008 — «policy violation»
# по RFC 6455: клиент должен понять, что переподключаться бессмысленно.
POLICY_VIOLATION = 1008


class Hub:
    """Кто сейчас в сети.

    Соединений у одного зрителя может быть несколько — открытые вкладки,
    телефон, — поэтому в значении множество, а не одно соединение.
    """

    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = defaultdict(set)

    def add(self, user_id: UUID, connection: WebSocket) -> None:
        self._connections[user_id].add(connection)

    def remove(self, user_id: UUID, connection: WebSocket) -> None:
        peers = self._connections.get(user_id)
        if peers is None:
            return
        peers.discard(connection)
        if not peers:
            # Пустое множество не оставляем: иначе словарь растёт по числу
            # когда-либо заходивших зрителей и не уменьшается никогда.
            del self._connections[user_id]

    async def push(self, user_id: UUID, payload: dict[str, Any]) -> int:
        """Отправляет сообщение во все соединения зрителя. Возвращает число доставленных."""
        delivered = 0
        for connection in list(self._connections.get(user_id, ())):
            try:
                await connection.send_json(payload)
            except (WebSocketDisconnect, RuntimeError):
                # Соединение оборвалось между проверкой и отправкой — обычное
                # дело: убираем его и считаем недоставленным.
                self.remove(user_id, connection)
                continue
            delivered += 1
        return delivered

    @property
    def online(self) -> int:
        return sum(len(peers) for peers in self._connections.values())


hub = Hub()
verifier = get_verifier(settings)

app = FastAPI(
    title=f'{settings.project_name}-ws',
    summary='Websocket-шлюз мгновенных уведомлений',
    docs_url=None,
    openapi_url=None,
)


class PushSchema(BaseModel):
    """Служебная доставка от воркера-отправщика."""

    user_id: UUID
    subject: str = Field(default='')
    body: str = Field(default='')
    template_code: str = Field(default='')


def require_service_token(
    token: Annotated[str | None, Header(alias=SERVICE_TOKEN_HEADER)] = None,
) -> None:
    """Пускает только воркера-отправщика.

    Raises:
        ServiceTokenInvalidError: секрет не задан или не совпал.
    """
    import secrets

    expected = settings.auth_service_token.get_secret_value()
    # Байты, а не строки: `compare_digest` на не-ASCII строке поднимает TypeError.
    if not expected or not token or not secrets.compare_digest(token.encode('utf-8'), expected.encode('utf-8')):
        raise ServiceTokenInvalidError


@app.exception_handler(ServiceError)
async def service_error_handler(_: object, exc: Exception) -> JSONResponse:
    """Ошибки отдаются в общем формате кинотеатра."""
    error = exc if isinstance(exc, ServiceError) else ServiceError()
    return JSONResponse(
        status_code=HTTPStatus.UNAUTHORIZED,
        content={'code': error.code, 'detail': error.message},
    )


@app.get('/health')
async def health() -> dict[str, str]:
    """Проверка живости для оркестратора."""
    return {'status': 'ok'}


@app.post('/internal/push')
async def push(body: PushSchema, _: Annotated[None, Depends(require_service_token)] = None) -> dict[str, int]:
    """Доставляет сообщение зрителю, если он сейчас в сети."""
    delivered = await hub.push(body.user_id, body.model_dump(mode='json'))
    if not delivered:
        logger.info('Зритель не в сети', extra={'user_id': str(body.user_id)})
    return {'delivered': delivered}


@app.websocket('/ws/notifications')
async def notifications(websocket: WebSocket, token: str | None = None) -> None:
    """Держит соединение зрителя, пока оно живо."""
    try:
        user = verifier.verify(f'Bearer {token}' if token else None)
    except ServiceError as error:
        # Соединение принимаем и сразу закрываем с понятным кодом: иначе
        # браузер увидит только «не удалось подключиться» и будет пробовать
        # снова в цикле.
        await websocket.accept()
        await websocket.close(code=POLICY_VIOLATION, reason=error.code)
        return

    await websocket.accept()
    hub.add(user.user_id, websocket)
    logger.info('Зритель подключился', extra={'user_id': str(user.user_id), 'online': hub.online})
    try:
        while True:
            # Входящие сообщения нам не нужны, но читать их надо: без чтения
            # приложение не заметит закрытие соединения.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(user.user_id, websocket)
        logger.info('Зритель отключился', extra={'user_id': str(user.user_id), 'online': hub.online})


if __name__ == '__main__':
    uvicorn.run(
        app,
        host='0.0.0.0',  # noqa: S104 - контейнер слушает свою сеть, наружу его выводит nginx
        port=8000,
        log_config=LOGGING,
        # Ровно один процесс, и это не оптимизация, а требование: реестр
        # открытых соединений живёт в памяти процесса. С двумя воркерами
        # зритель подключился бы к одному, а служебная доставка попала бы во
        # второй — и уведомление не дошло бы. Число задано явно, иначе
        # uvicorn возьмёт его из WEB_CONCURRENCY, заданного в образе для API.
        # Масштабировать шлюз горизонтально можно, но для этого нужна общая
        # шина между его копиями (например, Redis pub/sub) — этого здесь нет.
        workers=1,
        # Шлюз сам проверяет, живы ли соединения: без этого мёртвые копятся
        # до конца света.
        ws_ping_interval=settings.websocket_ping_interval,
    )
