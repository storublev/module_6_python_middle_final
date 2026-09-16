"""Клиент сервиса авторизации: разбор ответов, таймауты и прерыватель."""

from http import HTTPStatus
from uuid import UUID

import pytest
import requests

from tests.unit.fakes import FakeSession, make_response
from users.auth_client import (
    AuthClient,
    AuthServiceError,
    AuthServiceUnavailableError,
    InvalidCredentialsError,
    SessionExpiredError,
    Tokens,
    TooManyRequestsError,
)
from users.circuit_breaker import CircuitBreaker

BASE_URL = 'http://auth:8000'
USER_ID = '6f0c5c9c-4a55-4f6f-8f2a-2f1a5a8b0e11'
CONNECT_TIMEOUT = 1.0
READ_TIMEOUT = 3.0
BREAKER_FAILURES = 2


def build_client(session: FakeSession, breaker: CircuitBreaker | None = None) -> AuthClient:
    return AuthClient(
        base_url=BASE_URL,
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=READ_TIMEOUT,
        connect_retries=0,
        backoff_factor=0.0,
        breaker=breaker or CircuitBreaker(failures=BREAKER_FAILURES, reset_timeout=30.0),
        session=session,
    )


def test_login_returns_token_pair() -> None:
    """Успешный вход отдаёт пару токенов из ответа сервиса: ею живёт сессия сотрудника."""
    session = FakeSession([make_response(HTTPStatus.OK, {'access_token': 'a.b.c', 'refresh_token': 'r.s.t'})])

    assert build_client(session).login('neo', 'followtherabbit') == Tokens(access='a.b.c', refresh='r.s.t')


def test_login_sends_credentials_in_body_only() -> None:
    """Логин и пароль уходят только в теле запроса: в URL они попали бы в журналы."""
    session = FakeSession([make_response(HTTPStatus.OK, {'access_token': 'a.b.c', 'refresh_token': 'r.s.t'})])

    build_client(session).login('neo', 'followtherabbit')

    call = session.requests_log[0]
    assert call.url == f'{BASE_URL}/auth/api/v1/login'
    assert call.kwargs['json'] == {'login': 'neo', 'password': 'followtherabbit'}


def test_login_applies_both_timeouts() -> None:
    """У запроса явные таймауты соединения и ответа: сервис не подвесит форму входа."""
    session = FakeSession([make_response(HTTPStatus.OK, {'access_token': 'a.b.c', 'refresh_token': 'r.s.t'})])

    build_client(session).login('neo', 'followtherabbit')

    assert session.requests_log[0].kwargs['timeout'] == (CONNECT_TIMEOUT, READ_TIMEOUT)


def test_refresh_returns_new_pair() -> None:
    """Обновление отдаёт новую пару: прежний refresh-токен одноразовый."""
    session = FakeSession([make_response(HTTPStatus.OK, {'access_token': 'new.a', 'refresh_token': 'new.r'})])

    assert build_client(session).refresh('old.r') == Tokens(access='new.a', refresh='new.r')


def test_refresh_sends_token_in_body() -> None:
    """refresh-токен уходит в теле запроса: в URL он попал бы в журналы."""
    session = FakeSession([make_response(HTTPStatus.OK, {'access_token': 'new.a', 'refresh_token': 'new.r'})])

    build_client(session).refresh('old.r')

    call = session.requests_log[0]
    assert call.url == f'{BASE_URL}/auth/api/v1/token/refresh'
    assert call.kwargs['json'] == {'refresh_token': 'old.r'}


def test_refresh_of_closed_session_is_reported() -> None:
    """401 на обновление — сессия в сервисе закрыта: сотруднику пора выйти из админки."""
    session = FakeSession([make_response(HTTPStatus.UNAUTHORIZED, {'code': 'token_revoked'})])

    with pytest.raises(SessionExpiredError):
        build_client(session).refresh('old.r')


def test_expired_token_is_reported_separately() -> None:
    """401 на проверке права — истёкший или отозванный токен, а не сбой сервиса."""
    session = FakeSession([make_response(HTTPStatus.UNAUTHORIZED, {'code': 'token_expired'})])

    with pytest.raises(SessionExpiredError):
        build_client(session).check_permission('a.b.c', 'admin.access')


def test_expired_token_is_reported_for_profile() -> None:
    """401 на чтении данных сотрудника — тоже истёкший токен: его меняют по refresh."""
    session = FakeSession([make_response(HTTPStatus.UNAUTHORIZED, {'code': 'token_expired'})])

    with pytest.raises(SessionExpiredError):
        build_client(session).get_profile('a.b.c')


def test_wrong_password_raises_invalid_credentials() -> None:
    """401 от сервиса — неверные логин или пароль, а не сбой."""
    session = FakeSession([make_response(HTTPStatus.UNAUTHORIZED, {'code': 'invalid_credentials'})])

    with pytest.raises(InvalidCredentialsError):
        build_client(session).login('neo', 'wrong')


def test_exhausted_login_limit_raises_too_many_requests() -> None:
    """429 от сервиса отличается от неверного пароля: попытки исчерпаны."""
    session = FakeSession([make_response(HTTPStatus.TOO_MANY_REQUESTS, {'code': 'too_many_requests'})])

    with pytest.raises(TooManyRequestsError):
        build_client(session).login('neo', 'followtherabbit')


def test_server_error_raises_unavailable() -> None:
    """5xx — недоступность сервиса, а не отказ во входе."""
    session = FakeSession([make_response(HTTPStatus.INTERNAL_SERVER_ERROR)])

    with pytest.raises(AuthServiceUnavailableError):
        build_client(session).login('neo', 'followtherabbit')


def test_network_error_raises_unavailable() -> None:
    """Обрыв соединения наружу выходит исключением клиента, а не requests."""
    session = FakeSession([requests.ConnectionError('connection refused')])

    with pytest.raises(AuthServiceUnavailableError):
        build_client(session).login('neo', 'followtherabbit')


def test_unexpected_status_raises_service_error() -> None:
    """Неожиданный код ответа не молчит: вход отклоняется с ошибкой."""
    session = FakeSession([make_response(HTTPStatus.NOT_FOUND)])

    with pytest.raises(AuthServiceError):
        build_client(session).login('neo', 'followtherabbit')


def test_get_profile_reads_identity() -> None:
    """Профиль разбирается в идентификатор, логин и признак суперпользователя."""
    session = FakeSession([
        make_response(HTTPStatus.OK, {'id': USER_ID, 'login': 'neo', 'is_superuser': True, 'roles': []}),
    ])

    profile = build_client(session).get_profile('a.b.c')

    assert (profile.id, profile.login, profile.is_superuser) == (UUID(USER_ID), 'neo', True)


def test_get_profile_sends_bearer_token() -> None:
    """Токен передаётся заголовком Authorization, как требует сервис."""
    session = FakeSession([
        make_response(HTTPStatus.OK, {'id': USER_ID, 'login': 'neo', 'is_superuser': False, 'roles': []}),
    ])

    build_client(session).get_profile('a.b.c')

    assert session.requests_log[0].headers['Authorization'] == 'Bearer a.b.c'


def test_check_permission_returns_verdict() -> None:
    """Проверка права возвращает ответ сервиса без домыслов."""
    session = FakeSession([make_response(HTTPStatus.OK, {'user_id': USER_ID, 'permission': 'x.y', 'allowed': False})])

    assert build_client(session).check_permission('a.b.c', 'x.y') is False


def test_check_permission_bypasses_cache() -> None:
    """Право читается из базы (fresh=true): отозванный доступ закрывается сразу."""
    session = FakeSession([make_response(HTTPStatus.OK, {'user_id': USER_ID, 'permission': 'x.y', 'allowed': True})])

    build_client(session).check_permission('a.b.c', 'x.y')

    assert session.requests_log[0].kwargs['params'] == {'permission': 'x.y', 'fresh': 'true'}


def test_logout_closes_session() -> None:
    """Выход закрывает сессию в сервисе: токены админке больше не нужны."""
    session = FakeSession([make_response(HTTPStatus.NO_CONTENT)])

    build_client(session).logout('a.b.c')

    assert session.requests_log[0].url == f'{BASE_URL}/auth/api/v1/logout'


def test_logout_survives_unavailable_service() -> None:
    """Неудачный выход не мешает входу в админку: ошибка остаётся в журнале."""
    session = FakeSession([requests.ConnectionError('connection refused')])

    build_client(session).logout('a.b.c')


def test_breaker_opens_after_failures_in_a_row() -> None:
    """Серия сбоев размыкает прерыватель — следующий запрос в сеть не уходит."""
    session = FakeSession([requests.ConnectionError('down')] * BREAKER_FAILURES)
    client = build_client(session)
    for _ in range(BREAKER_FAILURES):
        with pytest.raises(AuthServiceUnavailableError):
            client.login('neo', 'followtherabbit')

    with pytest.raises(AuthServiceUnavailableError):
        client.login('neo', 'followtherabbit')

    assert len(session.requests_log) == BREAKER_FAILURES


def test_rejected_credentials_do_not_open_breaker() -> None:
    """Неверный пароль — не сбой сервиса: прерыватель от таких ответов не размыкается."""
    replies = [make_response(HTTPStatus.UNAUTHORIZED, {'code': 'invalid_credentials'})] * (BREAKER_FAILURES + 1)
    session = FakeSession(replies)
    client = build_client(session)

    for _ in range(BREAKER_FAILURES + 1):
        with pytest.raises(InvalidCredentialsError):
            client.login('neo', 'wrong')

    assert len(session.requests_log) == BREAKER_FAILURES + 1
