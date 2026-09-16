"""Приём идентификатора запроса на входе сервиса."""

from http import HTTPStatus

from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.request_id import HEADER, set_request_id

REQUEST_ID_REQUIRED = {
    'code': 'request_id_required',
    'detail': f'{HEADER} header is required; it is set by the gateway',
}
# Тег спана, по которому запрос ищется в Jaeger: тот же идентификатор виден и
# в журнале nginx, и в записях сервисов.
SPAN_ATTRIBUTE = 'http.request_id'


class RequestIdMiddleware:
    """Кладёт `X-Request-Id` в контекст запроса, в спан трассировки и в ответ.

    Заголовок ставит nginx, поэтому его отсутствие значит, что запрос пришёл
    мимо шлюза, — и по умолчанию такой запрос отклоняется: без идентификатора
    его не найти ни в журналах, ни в Jaeger. Проверку можно выключить
    настройкой: сервис, запущенный локально без nginx, иначе не отладить.

    Ответ отдаётся тем же идентификатором: клиент может назвать его в
    обращении в поддержку, и запрос найдётся.

    Написано как ASGI-middleware, а не через `BaseHTTPMiddleware`: тот
    оборачивает ответ в дополнительную задачу, и контекстная переменная,
    установленная в нём, до обработчика не доходит.
    """

    def __init__(self, app: ASGIApp, required: bool, exempt_paths: frozenset[str] = frozenset()):
        self.app = app
        self.required = required
        # Документация и проверка живости вызываются мимо nginx, в том числе
        # healthcheck'ом контейнера: требовать от них заголовок незачем.
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        request_id = self._header(scope)
        if not request_id and self.required and scope['path'] not in self.exempt_paths:
            await self._reject(send)
            return

        set_request_id(request_id or '-')
        if request_id:
            trace.get_current_span().set_attribute(SPAN_ATTRIBUTE, request_id)
        await self.app(scope, receive, self._with_header(send, request_id))

    @staticmethod
    def _header(scope: Scope) -> str:
        name = HEADER.lower().encode()
        for key, value in scope.get('headers', ()):
            if key == name:
                return value.decode('latin-1').strip()
        return ''

    @staticmethod
    def _with_header(send: Send, request_id: str) -> Send:
        if not request_id:
            return send

        async def inner(message: Message) -> None:
            if message['type'] == 'http.response.start':
                message = {
                    **message,
                    'headers': [*message.get('headers', []), (HEADER.lower().encode(), request_id.encode())],
                }
            await send(message)

        return inner

    @staticmethod
    async def _reject(send: Send) -> None:
        import json

        body = json.dumps(REQUEST_ID_REQUIRED).encode()
        await send({
            'type': 'http.response.start',
            'status': HTTPStatus.BAD_REQUEST,
            'headers': [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())],
        })
        await send({'type': 'http.response.body', 'body': body})
