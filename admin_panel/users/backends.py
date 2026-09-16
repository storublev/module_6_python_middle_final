"""Вход в админку через сервис авторизации.

Django проверяет логин и пароль перебором бэкендов из AUTHENTICATION_BACKENDS.
Этот бэкенд не сверяет пароль с локальной базой, а отдаёт его сервису
авторизации и по его ответу решает, пускать ли сотрудника:

1. POST /login — обмен логина и пароля на access-токен;
2. GET /users/me — кто это: идентификатор, логин, признак суперпользователя;
3. GET /access/check?permission=admin.access&fresh=true — можно ли ему в админку;
4. локальная запись сотрудника создаётся или обновляется по идентификатору;
5. POST /logout — сессия в сервисе авторизации закрывается: дальше сотрудника
   пускает сессия Django, а токены хранить негде.

Право проверяется при каждом входе, но не при каждом клике: срок доступа в
админку ограничен сроком сессии Django. Отозвать доступ немедленно можно,
деактивировав сотрудника в админке или закрыв его сессию.

Если сервис авторизации недоступен, бэкенд возвращает None и не поднимает
ошибку: Django пробует следующий бэкенд, и аварийный локальный
суперпользователь войти сможет.
"""

import logging

from django.contrib.auth.backends import BaseBackend
from django.db import IntegrityError, transaction
from django.http import HttpRequest

from users.auth_client import (
    AuthClient,
    AuthProfile,
    AuthServiceError,
    InvalidCredentialsError,
    TooManyRequestsError,
    build_client,
)
from users.models import User

logger = logging.getLogger(__name__)


class AuthServiceBackend(BaseBackend):
    def __init__(self, client: AuthClient | None = None) -> None:
        # Django создаёт бэкенд на каждый вход, но клиент с его прерывателем
        # должен быть общим для процесса, иначе счётчик сбоев обнулялся бы.
        self._client = client or _shared_client()

    def authenticate(
        self, request: HttpRequest | None, username: str | None = None, password: str | None = None, **kwargs,
    ) -> User | None:
        if not username or not password:
            return None
        try:
            access_token = self._client.login(username, password)
        except InvalidCredentialsError:
            return None
        except TooManyRequestsError:
            logger.warning('Вход в админку отклонён: исчерпан лимит попыток для %s', username)
            return None
        except AuthServiceError as exc:
            logger.warning('Сервис авторизации недоступен, вход в админку не проверен: %s', exc)
            return None

        try:
            profile = self._client.get_profile(access_token)
            allowed = profile.is_superuser or self._client.check_permission(
                access_token, _admin_permission(),
            )
        except AuthServiceError as exc:
            logger.warning('Сервис авторизации недоступен, права на админку не проверены: %s', exc)
            return None
        finally:
            self._client.logout(access_token)

        if not allowed:
            logger.info('Сотруднику %s отказано во входе в админку: нет права', profile.login)
            return None
        return _sync_user(profile)

    def get_user(self, user_id) -> User | None:
        """Пользователь текущей сессии. Вызывается на каждый запрос, поэтому только из базы."""
        return User.objects.filter(pk=user_id, is_active=True).first()


def _admin_permission() -> str:
    from django.conf import settings

    return settings.APP_SETTINGS.auth_admin_permission


def _sync_user(profile: AuthProfile) -> User | None:
    """Приводит локальную запись сотрудника в соответствие с сервисом авторизации."""
    try:
        with transaction.atomic():
            user, created = User.objects.update_or_create(
                id=profile.id,
                defaults={
                    'login': profile.login,
                    'is_active': True,
                    'is_staff': True,
                    'is_superuser': profile.is_superuser,
                },
            )
    except IntegrityError:
        # Логин занят другой записью — почти наверняка аварийным локальным
        # суперпользователем с таким же логином. Пускать нельзя: непонятно,
        # чья это учётная запись.
        logger.error('Логин %s занят другой записью админки, вход отклонён', profile.login)
        return None
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
        logger.info('Заведён сотрудник админки %s', user.login)
    return user


_client: AuthClient | None = None


def _shared_client() -> AuthClient:
    global _client
    if _client is None:
        _client = build_client()
    return _client
