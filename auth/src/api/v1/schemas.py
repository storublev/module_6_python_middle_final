"""Схемы запросов и ответов API. Описания и примеры полей попадают в OpenAPI."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.role import Permission, RoleName
from models.user import LOGIN_MAX_LENGTH, PASSWORD_MAX_LENGTH, Login, Password

LoginField = Annotated[
    Login,
    Field(description='Логин: латиница, цифры и символы _.@+-, регистр не учитывается', examples=['neo']),
]
PasswordField = Annotated[Password, Field(description='Пароль, от 8 до 128 символов', examples=['followtherabbit'])]
CurrentPassword = Annotated[
    str,
    Field(min_length=1, max_length=PASSWORD_MAX_LENGTH, description='Текущий пароль', examples=['followtherabbit']),
]
PermissionField = Annotated[Permission, Field(examples=['films.subscription'])]


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SignupSchema(BaseModel):
    """Регистрация."""

    login: LoginField
    password: PasswordField


class LoginSchema(BaseModel):
    """Вход по логину и паролю."""

    # Формат логина здесь не проверяется: неверный логин — это просто неудачный вход.
    login: str = Field(min_length=1, max_length=LOGIN_MAX_LENGTH, description='Логин', examples=['neo'])
    password: CurrentPassword


class RefreshSchema(BaseModel):
    """Обмен refresh-токена на новую пару токенов."""

    refresh_token: str = Field(min_length=1, description='refresh-токен из ответа на вход или прошлое обновление')


class TokenPairSchema(Schema):
    """Пара токенов. access-токен передаётся в заголовке `Authorization: Bearer <token>`."""

    access_token: str = Field(description='JWT для доступа к API, живёт недолго')
    refresh_token: str = Field(description='JWT для получения новой пары, одноразовый')
    token_type: str = Field(default='bearer', description='Тип токена для заголовка Authorization')
    expires_in: int = Field(description='Через сколько секунд истечёт access-токен', examples=[900])


class UserSchema(Schema):
    """Пользователь."""

    id: UUID = Field(description='Идентификатор')
    login: str = Field(description='Логин', examples=['neo'])
    created_at: datetime = Field(description='Когда зарегистрирован')


class RoleSchema(Schema):
    """Роль и её права."""

    id: UUID = Field(description='Идентификатор')
    name: str = Field(description='Имя роли', examples=['subscribers'])
    description: str | None = Field(description='Описание', examples=['Подписчики: фильмы по подписке'])
    permissions: list[str] = Field(description='Права роли', examples=[['films.subscription']])
    created_at: datetime = Field(description='Когда создана')
    updated_at: datetime = Field(description='Когда изменена')


class RoleShortSchema(Schema):
    """Роль пользователя."""

    id: UUID = Field(description='Идентификатор')
    name: str = Field(description='Имя роли', examples=['subscribers'])


class ProfileSchema(UserSchema):
    """Данные пользователя в личном кабинете."""

    is_superuser: bool = Field(description='Суперпользователю разрешено всё')
    roles: list[RoleShortSchema] = Field(description='Роли пользователя')


class ChangeLoginSchema(BaseModel):
    """Смена логина."""

    new_login: LoginField
    password: CurrentPassword


class ChangePasswordSchema(BaseModel):
    """Смена пароля.

    Текущий пароль не передаётся в одном случае: у пользователя, заведённого
    входом через соцсеть, пароля ещё нет, и он задаёт первый.
    """

    password: CurrentPassword | None = Field(
        default=None, description='Текущий пароль; не нужен, только если пароля ещё нет',
    )
    new_password: PasswordField


class LoginRecordSchema(Schema):
    """Вход в аккаунт."""

    user_agent: str | None = Field(description='User-Agent устройства', examples=['Mozilla/5.0 (Macintosh)'])
    ip: str | None = Field(description='IP-адрес', examples=['203.0.113.7'])
    created_at: datetime = Field(description='Время входа')


class RoleCreateSchema(BaseModel):
    """Новая роль."""

    name: RoleName = Field(description='Уникальное имя: латиница в нижнем регистре, цифры, _ и -',
                           examples=['subscribers'])
    description: str | None = Field(default=None, max_length=1000, description='Описание')
    permissions: list[PermissionField] = Field(default=[], max_length=100, description='Права роли')


class RoleUpdateSchema(BaseModel):
    """Изменение роли: меняются только переданные поля."""

    name: RoleName | None = Field(default=None, description='Новое имя', examples=['subscribers'])
    description: str | None = Field(default=None, max_length=1000, description='Новое описание')
    permissions: list[PermissionField] | None = Field(default=None, max_length=100,
                                                      description='Новый список прав целиком')

    @model_validator(mode='after')
    def forbid_null(self) -> 'RoleUpdateSchema':
        # Не передать поле — значит не менять его; а стереть можно только описание.
        for field in ('name', 'permissions'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self


class AccessCheckSchema(BaseModel):
    """Результат проверки права."""

    user_id: UUID | None = Field(description='Пользователь из токена; null — анонимный запрос')
    permission: str = Field(description='Проверенное право', examples=['films.subscription'])
    allowed: bool = Field(description='Есть ли у пользователя это право')


class ProviderSchema(BaseModel):
    """Соцсеть, через которую можно войти."""

    name: str = Field(description='Имя поставщика в адресах', examples=['yandex'])
    title: str = Field(description='Название для человека', examples=['Яндекс ID'])


class SocialAccountSchema(Schema):
    """Аккаунт в соцсети, привязанный к учётной записи."""

    provider: str = Field(description='Имя поставщика', examples=['yandex'])
    social_id: str = Field(description='Идентификатор аккаунта у поставщика', examples=['1234567890'])
    display_name: str | None = Field(description='Имя владельца в соцсети', examples=['Нео'])
    email: str | None = Field(description='Почта в соцсети', examples=['neo@example.com'])
    created_at: datetime = Field(description='Когда аккаунт привязан')


class SocialLoginSchema(Schema):
    """Итог возврата от поставщика: вход или привязка аккаунта."""

    tokens: TokenPairSchema | None = Field(description='Пара токенов; null — аккаунт привязан к текущему пользователю')
    account: SocialAccountSchema | None = Field(description='Привязанный аккаунт; null — это был вход')
    created: bool = Field(description='Учётная запись заведена этим входом')
