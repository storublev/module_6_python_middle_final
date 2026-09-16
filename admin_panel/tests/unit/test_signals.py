"""Выход из админки и сессия сотрудника в сервисе авторизации."""

from uuid import UUID

import pytest
from django.contrib.auth.signals import user_logged_out
from django.utils import timezone

from tests.unit.fakes import FakeAuthClient
from users.auth_client import AuthProfile, AuthServiceUnavailableError
from users.models import User

pytestmark = pytest.mark.django_db

USER_ID = UUID('6f0c5c9c-4a55-4f6f-8f2a-2f1a5a8b0e11')


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FakeAuthClient:
    """Сервис авторизации вместо настоящего: обработчик выхода берёт клиент по имени."""
    fake = FakeAuthClient(profile=AuthProfile(id=USER_ID, login='neo', is_superuser=False))
    monkeypatch.setattr('users.signals.shared_client', lambda: fake)
    return fake


@pytest.fixture
def staff(client: FakeAuthClient) -> User:
    return User.objects.create(
        id=USER_ID,
        login='neo',
        auth_access_token=client.tokens.access,
        auth_refresh_token=client.tokens.refresh,
        auth_checked_at=timezone.now(),
    )


def log_out(user: User) -> None:
    """Выход из админки — то же событие, что шлёт django.contrib.auth.logout."""
    user_logged_out.send(sender=User, request=None, user=user)


def test_logout_closes_session_in_auth_service(client: FakeAuthClient, staff: User) -> None:
    """Выход из админки закрывает и сессию сотрудника в сервисе авторизации."""
    log_out(staff)

    assert client.logged_out == [client.tokens.access]


def test_logout_forgets_tokens(client: FakeAuthClient, staff: User) -> None:
    """После выхода токенов у записи не остаётся: предъявлять больше нечего."""
    log_out(staff)

    saved = User.objects.get(pk=staff.pk)
    assert (saved.auth_access_token, saved.auth_refresh_token, saved.auth_checked_at) == ('', '', None)


def test_logout_of_local_superuser_asks_nothing(client: FakeAuthClient) -> None:
    """Выход аварийного локального суперпользователя в сервис авторизации не ходит."""
    local = User.objects.create_superuser(login='rescue', password='breakglass1234')

    log_out(local)

    assert client.logged_out == []


def test_unavailable_service_does_not_break_logout(client: FakeAuthClient, staff: User) -> None:
    """Недоступный сервис авторизации не мешает выйти из админки."""
    client.logout_error = AuthServiceUnavailableError('circuit breaker is open')

    log_out(staff)

    assert User.objects.get(pk=staff.pk).auth_access_token == ''
