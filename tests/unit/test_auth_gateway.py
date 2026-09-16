"""AuthAccessGateway: разбор ответов сервиса авторизации и поведение при его сбоях."""

from http import HTTPStatus

import httpx
import pytest

from storage.access import AccessUnavailableError, TokenRejectedError
from storage.auth import AuthAccessGateway
from storage.resilience import BackoffPolicy, CircuitBreaker

TOKEN = 'a.b.c'
PERMISSION = 'films.subscription'
BREAKER_FAILURES = 2
NO_RETRIES = BackoffPolicy(max_time=0, factor=0, max_value=0)


class Calls:
    """Запоминает запросы, которые ушли бы в сервис авторизации."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __len__(self) -> int:
        return len(self.requests)


def build_gateway(handler, breaker: CircuitBreaker | None = None) -> tuple[AuthAccessGateway, Calls]:
    """Шлюз поверх подменённого транспорта httpx: сети нет, поведение настоящее."""
    calls = Calls()

    def record(request: httpx.Request) -> httpx.Response:
        calls.requests.append(request)
        return handler(request)

    client = httpx.AsyncClient(base_url='http://auth:8000', transport=httpx.MockTransport(record))
    gateway = AuthAccessGateway(
        client,
        retry=NO_RETRIES,
        breaker=breaker or CircuitBreaker(failures=BREAKER_FAILURES, reset_timeout=30),
    )
    return gateway, calls


def answer(status: int, payload=None):
    return lambda _: httpx.Response(status, json=payload)


@pytest.mark.parametrize('allowed', [True, False])
async def test_verdict_is_taken_from_auth_service(allowed):
    """Ответ сервиса о праве возвращается как есть, без домыслов."""
    gateway, _ = build_gateway(answer(HTTPStatus.OK, {'user_id': None, 'permission': PERMISSION, 'allowed': allowed}))

    assert await gateway.check(TOKEN, PERMISSION) is allowed


async def test_token_goes_in_authorization_header():
    """Токен передаётся заголовком Authorization, как требует сервис авторизации."""
    gateway, calls = build_gateway(answer(HTTPStatus.OK, {'allowed': True}))

    await gateway.check(TOKEN, PERMISSION)

    assert calls.requests[0].headers['Authorization'] == f'Bearer {TOKEN}'


async def test_permission_is_checked_bypassing_cache():
    """Право читается из базы (fresh=true): отозванная подписка закрывает доступ сразу."""
    gateway, calls = build_gateway(answer(HTTPStatus.OK, {'allowed': True}))

    await gateway.check(TOKEN, PERMISSION)

    assert dict(calls.requests[0].url.params) == {'permission': PERMISSION, 'fresh': 'true'}


async def test_rejected_token_is_reported_with_its_code():
    """401 — отказ по токену: наружу идут код и текст сервиса авторизации, а не «прав нет»."""
    gateway, _ = build_gateway(
        answer(HTTPStatus.UNAUTHORIZED, {'code': 'token_expired', 'detail': 'Token has expired'}),
    )

    with pytest.raises(TokenRejectedError) as exc_info:
        await gateway.check(TOKEN, PERMISSION)

    assert (exc_info.value.code, exc_info.value.detail) == ('token_expired', 'Token has expired')


async def test_server_error_is_unavailability():
    """5xx — недоступность сервиса, а не отказ в праве."""
    gateway, _ = build_gateway(answer(HTTPStatus.INTERNAL_SERVER_ERROR))

    with pytest.raises(AccessUnavailableError):
        await gateway.check(TOKEN, PERMISSION)


async def test_network_error_is_unavailability():
    """Сбой сети наружу выходит ошибкой шлюза: httpx за его пределы не протекает."""

    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('connection refused')

    gateway, _ = build_gateway(fail)

    with pytest.raises(AccessUnavailableError):
        await gateway.check(TOKEN, PERMISSION)


async def test_unexpected_status_is_unavailability():
    """Неожиданный код ответа не принимается за отказ в праве."""
    gateway, _ = build_gateway(answer(HTTPStatus.NOT_FOUND))

    with pytest.raises(AccessUnavailableError):
        await gateway.check(TOKEN, PERMISSION)


async def test_breaker_opens_after_failures_in_a_row():
    """Серия сбоев размыкает прерыватель — следующий запрос в сеть не уходит."""

    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('down')

    gateway, calls = build_gateway(fail)
    for _ in range(BREAKER_FAILURES + 1):
        with pytest.raises(AccessUnavailableError):
            await gateway.check(TOKEN, PERMISSION)

    assert len(calls) == BREAKER_FAILURES


async def test_denied_permission_does_not_open_breaker():
    """«Права нет» — нормальный ответ живого сервиса: прерыватель от него не размыкается."""
    gateway, calls = build_gateway(answer(HTTPStatus.OK, {'allowed': False}))

    for _ in range(BREAKER_FAILURES + 1):
        await gateway.check(TOKEN, PERMISSION)

    assert len(calls) == BREAKER_FAILURES + 1


async def test_rejected_token_does_not_open_breaker():
    """Истёкший токен — тоже ответ живого сервиса, а не его сбой."""
    gateway, calls = build_gateway(answer(HTTPStatus.UNAUTHORIZED, {'code': 'token_expired', 'detail': 'expired'}))

    for _ in range(BREAKER_FAILURES + 1):
        with pytest.raises(TokenRejectedError):
            await gateway.check(TOKEN, PERMISSION)

    assert len(calls) == BREAKER_FAILURES + 1
