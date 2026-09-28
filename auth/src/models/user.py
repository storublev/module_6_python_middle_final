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
    """Учётная запись. Пароль хранится только в виде хеша, а может и отсутствовать."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    login: str
    # None — пароля нет: учётную запись завёл вход через соцсеть. Такой
    # пользователь входит только соцсетью, пока сам не задаст пароль.
    password_hash: str | None
    # Растёт при смене пароля: сессии, открытые с прежней версией, не действуют.
    credentials_version: int
    is_superuser: bool
    created_at: datetime
    # Контакты для уведомлений. Необязательные: учётная запись, заведённая до
    # появления сервиса уведомлений или входом через соцсеть, их не имеет.
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    timezone: str | None = None

    @property
    def has_password(self) -> bool:
        """Может ли пользователь войти по логину и паролю."""
        return self.password_hash is not None


class Contact(BaseModel):
    """Всё, что нужно, чтобы написать пользователю письмо.

    Отдельная модель, а не `User`: наружу, в сервис уведомлений, уезжает
    минимальный набор бизнес-данных, а не то, что лежит в таблице. Так учит
    урок про отчётные события — контракт не должен повторять схему базы,
    иначе её нельзя будет менять.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    login: str
    email: str | None
    first_name: str | None
    last_name: str | None
    timezone: str | None

    @property
    def full_name(self) -> str:
        """Имя для обращения в письме; пустая строка, если имени нет."""
        return ' '.join(part for part in (self.first_name, self.last_name) if part)


class ProfileUpdate(BaseModel):
    """Что пользователь может поменять в своём профиле.

    Значение None означает «не трогать это поле», поэтому очистить поле через
    этот объект нельзя — для очистки пользователь присылает пустую строку,
    которую проверка приводит к None на уровне API.
    """

    model_config = ConfigDict(frozen=True)

    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    timezone: str | None = None


class LoginRecord(BaseModel):
    """Запись истории входов: когда и с какого устройства входили в аккаунт."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    user_agent: str | None
    ip: str | None
    created_at: datetime
