from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

# Логин сравнивается без учёта регистра: «Neo» и «neo» — один пользователь.
# Поэтому он хранится в нижнем регистре, а приводится к нему при каждом вводе.
# Шаблон проверяется до приведения, поэтому допускает заглавные буквы.
LOGIN_PATTERN = r'^[A-Za-z0-9_.@+-]+$'
LOGIN_MIN_LENGTH = 3
LOGIN_MAX_LENGTH = 64
PASSWORD_MIN_LENGTH = 8
# Длиннее пароли не нужны, а хешировать мегабайты по запросу клиента — лишняя нагрузка.
PASSWORD_MAX_LENGTH = 128

Login = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=LOGIN_MIN_LENGTH,
        max_length=LOGIN_MAX_LENGTH,
        pattern=LOGIN_PATTERN,
    ),
]
Password = Annotated[str, StringConstraints(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


def normalize_login(login: str) -> str:
    """Приводит логин к виду, в котором он хранится."""
    return login.strip().lower()


class User(BaseModel):
    """Учётная запись. Пароль хранится только в виде хеша."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    login: str
    password_hash: str
    is_superuser: bool
    created_at: datetime


class LoginRecord(BaseModel):
    """Запись истории входов: когда и с какого устройства входили в аккаунт."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    user_agent: str | None
    ip: str | None
    created_at: datetime
