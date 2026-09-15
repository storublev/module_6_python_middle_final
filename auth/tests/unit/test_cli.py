"""Консольная команда создания суперпользователя."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from typer.testing import CliRunner

import cli
from services.auth import RegistrationService
from services.passwords import PasswordHasher
from storage.base import StorageUnavailableError
from tests.unit.fakes import Database, FakeUserRepository

runner = CliRunner()


@pytest.fixture
def db(monkeypatch, passwords: PasswordHasher) -> Database:
    """Команда работает с базой в памяти вместо PostgreSQL."""
    database = Database()

    @asynccontextmanager
    async def factory() -> AsyncIterator[RegistrationService]:
        yield RegistrationService(FakeUserRepository(database), passwords)

    monkeypatch.setattr(cli, 'service_factory', factory)
    return database


def test_createsuperuser_with_options(db: Database) -> None:
    """Логин приводится к нижнему регистру, пользователь создаётся суперпользователем."""
    result = runner.invoke(cli.app, ['createsuperuser', '--login', 'Admin'],
                           env={'AUTH_SUPERUSER_PASSWORD': 'supersecret'})

    assert result.exit_code == 0, result.output
    [user] = db.users.values()
    assert (user.login, user.is_superuser) == ('admin', True)


def test_createsuperuser_prompts_for_password(db: Database) -> None:
    """Без переменной окружения пароль спрашивается дважды, без отображения."""
    result = runner.invoke(cli.app, ['createsuperuser'], input='admin\nsupersecret\nsupersecret\n')

    assert result.exit_code == 0, result.output
    assert 'supersecret' not in result.output
    assert [user.login for user in db.users.values()] == ['admin']


def test_createsuperuser_taken_login(db: Database) -> None:
    """Занятый логин — код выхода 1, второй пользователь не создаётся."""
    args = ['createsuperuser', '--login', 'admin']
    runner.invoke(cli.app, args, env={'AUTH_SUPERUSER_PASSWORD': 'supersecret'})

    result = runner.invoke(cli.app, args, env={'AUTH_SUPERUSER_PASSWORD': 'supersecret'})

    assert result.exit_code == 1
    assert 'уже занят' in result.output
    assert len(db.users) == 1


@pytest.mark.parametrize('login, password', [('ad', 'supersecret'), ('admin!', 'supersecret'), ('admin', 'short')])
def test_createsuperuser_validates_input(db: Database, login: str, password: str) -> None:
    """Логин и пароль проверяются по тем же правилам, что при регистрации."""
    result = runner.invoke(cli.app, ['createsuperuser', '--login', login], env={'AUTH_SUPERUSER_PASSWORD': password})

    assert result.exit_code == 2
    assert db.users == {}


def test_createsuperuser_database_unavailable(monkeypatch) -> None:
    """Недоступная база — понятное сообщение и код выхода 2."""
    @asynccontextmanager
    async def factory() -> AsyncIterator[RegistrationService]:
        raise StorageUnavailableError('PostgreSQL: connection refused')
        yield

    monkeypatch.setattr(cli, 'service_factory', factory)

    result = runner.invoke(cli.app, ['createsuperuser', '--login', 'admin'], env={'AUTH_SUPERUSER_PASSWORD': 'x' * 8})

    assert result.exit_code == 2
    assert 'недоступна' in result.output
