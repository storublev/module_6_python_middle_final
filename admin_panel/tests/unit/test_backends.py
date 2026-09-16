"""Бэкенд аутентификации админки: кого пускать и что записывать в базу."""

from uuid import UUID, uuid4

import pytest

from tests.unit.fakes import FakeAuthClient
from users.auth_client import (
    AuthProfile,
    AuthServiceUnavailableError,
    InvalidCredentialsError,
    TooManyRequestsError,
)
from users.backends import AuthServiceBackend
from users.models import User

pytestmark = pytest.mark.django_db

USER_ID = UUID('6f0c5c9c-4a55-4f6f-8f2a-2f1a5a8b0e11')
PASSWORD = 'followtherabbit'


@pytest.fixture
def profile() -> AuthProfile:
    return AuthProfile(id=USER_ID, login='neo', is_superuser=False)


@pytest.fixture
def client(profile: AuthProfile) -> FakeAuthClient:
    return FakeAuthClient(profile=profile)


@pytest.fixture
def backend(client: FakeAuthClient) -> AuthServiceBackend:
    return AuthServiceBackend(client=client)


def test_staff_with_permission_is_let_in(backend: AuthServiceBackend) -> None:
    """Сотрудника с правом на админку пускают."""
    user = backend.authenticate(None, username='neo', password=PASSWORD)

    assert user is not None
    assert user.login == 'neo'


def test_user_record_keeps_identifier_from_auth_service(backend: AuthServiceBackend) -> None:
    """Запись сотрудника заводится с идентификатором из сервиса авторизации."""
    user = backend.authenticate(None, username='neo', password=PASSWORD)

    assert user.id == USER_ID


def test_user_without_permission_is_rejected(client: FakeAuthClient, backend: AuthServiceBackend) -> None:
    """Пользователь без права admin.access в админку не попадает."""
    client.allowed = False

    assert backend.authenticate(None, username='neo', password=PASSWORD) is None


def test_rejected_user_is_not_saved(client: FakeAuthClient, backend: AuthServiceBackend) -> None:
    """Отказ во входе не заводит запись: в админке только те, кому вход разрешён."""
    client.allowed = False

    backend.authenticate(None, username='neo', password=PASSWORD)

    assert not User.objects.exists()


def test_superuser_is_let_in_without_permission(
    client: FakeAuthClient, backend: AuthServiceBackend, profile: AuthProfile,
) -> None:
    """Суперпользователю сервиса авторизации разрешено всё, отдельное право ему не нужно."""
    client.allowed = False
    client.profile = AuthProfile(id=profile.id, login=profile.login, is_superuser=True)

    user = backend.authenticate(None, username='neo', password=PASSWORD)

    assert user is not None
    assert user.is_superuser


def test_wrong_password_is_rejected(client: FakeAuthClient, backend: AuthServiceBackend) -> None:
    """Неверный пароль — отказ; следующие бэкенды Django ещё могут попробовать."""
    client.login_error = InvalidCredentialsError('invalid_credentials')

    assert backend.authenticate(None, username='neo', password='wrong') is None


def test_exhausted_attempts_are_rejected(client: FakeAuthClient, backend: AuthServiceBackend) -> None:
    """Исчерпанный лимит попыток входа — отказ, а не ошибка страницы."""
    client.login_error = TooManyRequestsError('too_many_requests')

    assert backend.authenticate(None, username='neo', password=PASSWORD) is None


def test_unavailable_service_is_rejected_without_error(client: FakeAuthClient, backend: AuthServiceBackend) -> None:
    """Недоступность сервиса не роняет вход: бэкенд отказывает, аварийный вход остаётся."""
    client.login_error = AuthServiceUnavailableError('circuit breaker is open')

    assert backend.authenticate(None, username='neo', password=PASSWORD) is None


def test_empty_credentials_do_not_reach_auth_service(client: FakeAuthClient, backend: AuthServiceBackend) -> None:
    """Пустая форма входа не тратит попытку в сервисе авторизации."""
    client.login_error = AssertionError('запрос не должен был уйти')

    assert backend.authenticate(None, username='', password='') is None


def test_session_in_auth_service_is_kept(client: FakeAuthClient, backend: AuthServiceBackend) -> None:
    """После входа сессия в сервисе остаётся: ею админка перепроверяет доступ сотрудника."""
    user = backend.authenticate(None, username='neo', password=PASSWORD)

    assert client.logged_out == []
    assert (user.auth_access_token, user.auth_refresh_token) == (client.tokens.access, client.tokens.refresh)


def test_login_remembers_when_access_was_checked(backend: AuthServiceBackend) -> None:
    """Вход — тоже проверка доступа: её время запоминается, чтобы не перепроверять сразу же."""
    user = backend.authenticate(None, username='neo', password=PASSWORD)

    assert user.auth_checked_at is not None


def test_session_is_closed_when_permission_is_missing(
    client: FakeAuthClient, backend: AuthServiceBackend,
) -> None:
    """Отказ во входе не оставляет открытую сессию в сервисе авторизации."""
    client.allowed = False

    backend.authenticate(None, username='neo', password=PASSWORD)

    assert client.logged_out == [client.token]


def test_session_is_closed_even_when_permission_check_fails(
    client: FakeAuthClient, backend: AuthServiceBackend,
) -> None:
    """Сбой при проверке права не оставляет открытую сессию в сервисе авторизации."""
    client.profile_error = AuthServiceUnavailableError('timeout')

    backend.authenticate(None, username='neo', password=PASSWORD)

    assert client.logged_out == [client.token]


def test_password_is_not_stored_locally(backend: AuthServiceBackend) -> None:
    """Пароль сотрудника в базе админки не хранится: войти им локально нельзя."""
    user = backend.authenticate(None, username='neo', password=PASSWORD)

    assert not user.has_usable_password()


def test_repeated_login_updates_existing_record(
    client: FakeAuthClient, backend: AuthServiceBackend, profile: AuthProfile,
) -> None:
    """Повторный вход обновляет запись, а не плодит новые: логин и права приезжают из сервиса."""
    backend.authenticate(None, username='neo', password=PASSWORD)
    client.profile = AuthProfile(id=profile.id, login='trinity', is_superuser=True)

    user = backend.authenticate(None, username='trinity', password=PASSWORD)

    assert User.objects.count() == 1
    assert (user.login, user.is_superuser) == ('trinity', True)


def test_login_taken_by_another_record_is_rejected(client: FakeAuthClient, backend: AuthServiceBackend) -> None:
    """Если логин занят другой записью админки, вход отклоняется, а не роняет страницу."""
    User.objects.create_superuser(login='neo', password=PASSWORD)

    assert backend.authenticate(None, username='neo', password=PASSWORD) is None


def test_deactivated_staff_is_not_restored_by_get_user(backend: AuthServiceBackend) -> None:
    """Отключённый в админке сотрудник не восстанавливается из сессии: доступ закрыт сразу."""
    user = backend.authenticate(None, username='neo', password=PASSWORD)
    User.objects.filter(pk=user.pk).update(is_active=False)

    assert backend.get_user(user.pk) is None


def test_get_user_returns_active_staff(backend: AuthServiceBackend) -> None:
    """Пользователя сессии бэкенд отдаёт по идентификатору из локальной базы."""
    user = backend.authenticate(None, username='neo', password=PASSWORD)

    assert backend.get_user(user.pk) == user


def test_get_user_ignores_unknown_identifier(backend: AuthServiceBackend) -> None:
    """Неизвестный идентификатор в сессии не даёт доступа."""
    assert backend.get_user(uuid4()) is None
