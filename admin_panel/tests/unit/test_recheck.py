"""Перепроверка доступа сотрудника, пока он работает в админке."""

from datetime import timedelta
from uuid import UUID

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from tests.unit.fakes import FakeAuthClient
from users.auth_client import AuthProfile, AuthServiceUnavailableError, SessionExpiredError, Tokens
from users.models import User
from users.recheck import AccessRecheckMiddleware

pytestmark = pytest.mark.django_db

USER_ID = UUID('6f0c5c9c-4a55-4f6f-8f2a-2f1a5a8b0e11')
PERMISSION = 'admin.access'


@pytest.fixture
def client() -> FakeAuthClient:
    return FakeAuthClient(profile=AuthProfile(id=USER_ID, login='neo', is_superuser=False))


@pytest.fixture
def staff(client: FakeAuthClient) -> User:
    """Сотрудник, только что вошедший через сервис авторизации."""
    return User.objects.create(
        id=USER_ID,
        login='neo',
        auth_access_token=client.tokens.access,
        auth_refresh_token=client.tokens.refresh,
        auth_checked_at=timezone.now(),
    )


@pytest.fixture
def middleware(client: FakeAuthClient) -> AccessRecheckMiddleware:
    return AccessRecheckMiddleware(lambda request: HttpResponse(), client=client)


def checked_ago(staff: User, seconds: float) -> None:
    """Отодвигает время последней проверки доступа в прошлое."""
    User.objects.filter(pk=staff.pk).update(auth_checked_at=timezone.now() - timedelta(seconds=seconds))


def stale(staff: User) -> None:
    """Время перепроверить: интервал с прошлой проверки прошёл."""
    checked_ago(staff, 61)


def test_fresh_check_does_not_ask_auth_service(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Сразу после входа доступ не перепроверяется: каждый запрос страницы не ходит в сервис."""
    assert middleware.allowed(staff.pk)
    assert client.permission_checks == []


def test_access_is_rechecked_after_the_interval(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Через интервал право спрашивается заново."""
    stale(staff)

    assert middleware.allowed(staff.pk)
    assert client.permission_checks == [PERMISSION]


def test_revoked_permission_closes_admin_session(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Отобранное право закрывает доступ к админке, не дожидаясь конца сессии Django."""
    stale(staff)
    client.allowed = False

    assert not middleware.allowed(staff.pk)


def test_revoked_permission_closes_session_in_auth_service(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Оставшаяся не у дел сессия в сервисе авторизации закрывается, а токены забываются."""
    stale(staff)
    client.allowed = False

    middleware.allowed(staff.pk)

    assert client.logged_out == [client.tokens.access]
    assert User.objects.get(pk=staff.pk).auth_refresh_token == ''


def test_expired_access_token_is_refreshed(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Истёкший access-токен меняется по refresh, и работа продолжается."""
    stale(staff)
    old_refresh = client.tokens.refresh
    client.tokens = Tokens(access='rotated-access', refresh='rotated-refresh')

    assert middleware.allowed(staff.pk)
    assert client.refreshed == [old_refresh]


def test_refreshed_tokens_are_saved(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Новая пара токенов сохраняется: следующая проверка идёт уже с ней."""
    stale(staff)
    client.tokens = Tokens(access='rotated-access', refresh='rotated-refresh')
    client.next_tokens = Tokens(access='fresh-access', refresh='fresh-refresh')

    middleware.allowed(staff.pk)

    saved = User.objects.get(pk=staff.pk)
    assert (saved.auth_access_token, saved.auth_refresh_token) == ('fresh-access', 'fresh-refresh')


def test_closed_auth_session_ends_admin_session(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Смена пароля или выход со всех устройств закрывают сессию в сервисе — и админку тоже."""
    stale(staff)
    client.tokens = Tokens(access='rotated-access', refresh='rotated-refresh')
    client.refresh_error = SessionExpiredError('token_revoked')

    assert not middleware.allowed(staff.pk)
    assert User.objects.get(pk=staff.pk).auth_access_token == ''


def test_unavailable_service_is_tolerated_for_a_while(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Короткий сбой сервиса авторизации не выгоняет сотрудника: действует прошлая проверка."""
    stale(staff)
    client.profile_error = AuthServiceUnavailableError('circuit breaker is open')

    assert middleware.allowed(staff.pk)


def test_long_unavailability_ends_the_session(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Если перепроверить доступ не удаётся дольше допустимого, сессия админки закрывается."""
    checked_ago(staff, 301)
    client.profile_error = AuthServiceUnavailableError('circuit breaker is open')

    assert not middleware.allowed(staff.pk)


def test_deactivated_staff_is_not_allowed(middleware: AccessRecheckMiddleware, staff: User) -> None:
    """Отключённый в админке сотрудник теряет доступ на ближайшем же запросе."""
    User.objects.filter(pk=staff.pk).update(is_active=False)

    assert not middleware.allowed(staff.pk)


def test_local_superuser_is_not_checked_in_auth_service(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient,
) -> None:
    """Аварийный локальный суперпользователь работает и без сервиса авторизации: он его не заводил."""
    local = User.objects.create_superuser(login='rescue', password='breakglass1234')

    assert middleware.allowed(local.pk)
    assert client.permission_checks == []


def test_profile_changes_reach_the_local_record(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Перепроверка обновляет логин и признак суперпользователя: они могли измениться в сервисе."""
    stale(staff)
    client.profile = AuthProfile(id=USER_ID, login='theone', is_superuser=True)

    middleware.allowed(staff.pk)

    saved = User.objects.get(pk=staff.pk)
    assert (saved.login, saved.is_superuser) == ('theone', True)


def test_request_of_revoked_staff_becomes_anonymous(
    middleware: AccessRecheckMiddleware, client: FakeAuthClient, staff: User,
) -> None:
    """Потерявший доступ сотрудник выходит из админки прямо на этом запросе."""
    stale(staff)
    client.allowed = False
    request = RequestFactory().get('/admin/')
    request.session = SessionStore()
    request.user = staff

    middleware(request)

    assert not request.user.is_authenticated


def test_request_of_valid_staff_goes_through(
    middleware: AccessRecheckMiddleware, staff: User,
) -> None:
    """Пока доступ есть, запрос идёт дальше, а сотрудник остаётся в своей сессии."""
    stale(staff)
    request = RequestFactory().get('/admin/')
    request.session = SessionStore()
    request.user = staff

    response = middleware(request)

    assert response.status_code == 200
    assert request.user.is_authenticated
