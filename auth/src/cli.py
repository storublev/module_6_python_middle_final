"""Консольные команды сервиса авторизации.

    python cli.py createsuperuser --login admin

Пароль спрашивается без отображения на экране или берётся из переменной
AUTH_SUPERUSER_PASSWORD — так команду можно запустить без терминала.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated

import typer
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import settings
from db.postgres import create_engine
from models.user import Login, Password
from services.auth import RegistrationService
from services.errors import LoginTakenError
from services.passwords import PasswordHasher
from storage.base import StorageUnavailableError
from storage.postgres import PostgresUserRepository

app = typer.Typer(help='Команды сервиса авторизации.', no_args_is_help=True)


@app.callback()
def main() -> None:
    """Команды сервиса авторизации."""
    # Без callback Typer запустил бы единственную команду без её имени.


@asynccontextmanager
async def registration_service() -> AsyncIterator[RegistrationService]:
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield RegistrationService(PostgresUserRepository(session), PasswordHasher())
    finally:
        await engine.dispose()


# Фабрика сервиса подменяется в тестах, чтобы не ходить в PostgreSQL.
ServiceFactory = Callable[[], AbstractAsyncContextManager[RegistrationService]]
service_factory: ServiceFactory = registration_service


def validate(adapter: TypeAdapter, value: str, field: str) -> str:
    try:
        validated = adapter.validate_python(value)
    except ValidationError as exc:
        raise typer.BadParameter(exc.errors()[0]['msg'], param_hint=field) from exc
    return validated


async def create_superuser(login: str, password: str) -> None:
    async with service_factory() as registration:
        user = await registration.register(login, password, is_superuser=True)
    typer.echo(f'Суперпользователь {user.login} создан, id {user.id}')


@app.command('createsuperuser')
def createsuperuser(
    login: Annotated[str, typer.Option(prompt='Логин', help='Логин суперпользователя')],
    password: Annotated[
        str,
        typer.Option(
            prompt='Пароль',
            hide_input=True,
            confirmation_prompt=True,
            envvar='AUTH_SUPERUSER_PASSWORD',
            help='Пароль; по умолчанию спрашивается в терминале',
        ),
    ],
) -> None:
    """Создаёт суперпользователя: ему разрешены все действия в системе."""
    login = validate(TypeAdapter(Login), login, '--login')
    password = validate(TypeAdapter(Password), password, '--password')
    try:
        asyncio.run(create_superuser(login, password))
    except LoginTakenError:
        typer.echo(f'Логин {login} уже занят', err=True)
        raise typer.Exit(code=1) from None
    except StorageUnavailableError as exc:
        typer.echo(f'База данных недоступна: {exc}', err=True)
        raise typer.Exit(code=2) from exc


if __name__ == '__main__':
    app()
