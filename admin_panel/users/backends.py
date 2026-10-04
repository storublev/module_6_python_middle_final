"""Вход в админку через сервис авторизации.

Django проверяет логин и пароль перебором бэкендов из AUTHENTICATION_BACKENDS.
Этот бэкенд не сверяет пароль с локальной базой, а отдаёт его сервису
авторизации и по его ответу решает, пускать ли сотрудника:

1. POST /login — обмен логина и пароля на пару токенов;
2. GET /users/me — кто это: идентификатор, логин, признак суперпользователя;
3. GET /access/check?permission=admin.access&fresh=true — можно ли ему в админку;
4. локальная запись сотрудника создаётся или обновляется по идентификатору,
   вместе с ней сохраняется пара токенов.

Сессия в сервисе авторизации при удачном входе не закрывается: ею админка
перепроверяет доступ сотрудника, пока он работает (users/recheck.py), а при
выходе закрывает (users/signals.py). Если же вход не состоялся — права не
хватило или проверить их не удалось, — открытая сессия тут же закрывается.

Отозвать доступ немедленно, не дожидаясь перепроверки, можно, деактивировав
сотрудника в админке: его сессия закрывается на ближайшем же запросе.

Если сервис авторизации недоступен, бэкенд возвращает None и не поднимает
ошибку: Django пробует следующий бэкенд, и аварийный локальный
суперпользователь войти сможет.
"""

import logging

from django.contrib.auth.backends import BaseBackend
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone

from users.auth_client import (
    AuthClient,
    AuthProfile,
    AuthServiceError,
    InvalidCredentialsError,
    Tokens,
    TooManyRequestsError,
    shared_client,
)
from users.models import User

logger = logging.getLogger(__name__)


class AuthServiceBackend(BaseBackend):
    def __init__(self, client: AuthClient | None = None) -> None:
        # Django создаёт бэкенд на каждый вход, но клиент с его прерывателем
        # должен быть общим для процесса, иначе счётчик сбоев обнулялся бы.
        self._client = client or shared_client()

    def authenticate(
        self, request: HttpRequest | None, username: str | None = None, password: str | None = None, **kwargs,
    ) -> User | None:
        if not username or not password:
            return None
        try:
            tokens = self._client.login(username, password)
        except InvalidCredentialsError:
            return None
        except TooManyRequestsError:
            logger.warning('Вход в админку отклонён: исчерпан лимит попыток для %s', username)
            return None
        except AuthServiceError as exc:
            logger.warning('Сервис авторизации недоступен, вход в админку не проверен: %s', exc)
            return None

        try:
            profile = self._client.get_profile(tokens.access)
            allowed = profile.is_superuser or self._client.check_permission(
                tokens.access, _admin_permission(),
            )
        except AuthServiceError as exc:
            logger.warning('Сервис авторизации недоступен, права на админку не проверены: %s', exc)
            self._client.logout(tokens.access)
            return None

        if not allowed:
            logger.info('Сотруднику %s отказано во входе в админку: нет права', profile.login)
            self._client.logout(tokens.access)
            return None
        user = _sync_user(profile, tokens)
        if user is None:
            # Запись завести не удалось — сессия в сервисе авторизации не нужна.
            self._client.logout(tokens.access)
        return user

    def get_user(self, user_id) -> User | None:
        """Пользователь текущей сессии. Вызывается на каждый запрос, поэтому только из базы."""
        return User.objects.filter(pk=user_id, is_active=True).first()


def _admin_permission() -> str:
    from django.conf import settings

    return settings.APP_SETTINGS.auth_admin_permission


def _sync_user(profile: AuthProfile, tokens: Tokens) -> User | None:
    """Приводит локальную запись сотрудника в соответствие с сервисом авторизации.

    Вместе с данными сохраняется сессия сотрудника в сервисе авторизации: ею
    админка перепроверяет его доступ, пока он работает.

    Признак «активен» принадлежит админке, а не сервису авторизации: им
    закрывают доступ немедленно, не дожидаясь, пока в сервисе отберут право.
    Поэтому у существующей записи он не трогается, а отключённого сотрудника
    вход не пускает и не включает обратно; True ставится только новой записи.
    """
    fields = {
        'login': profile.login,
        'is_staff': True,
        'is_superuser': profile.is_superuser,
        'auth_access_token': tokens.access,
        'auth_refresh_token': tokens.refresh,
        'auth_checked_at': timezone.now(),
    }
    try:
        with transaction.atomic():
            existing = User.objects.select_for_update().filter(pk=profile.id).first()
            if existing is not None and not existing.is_active:
                logger.info('Сотрудник %s отключён в админке, вход отклонён', existing.login)
                return None
            user, created = User.objects.update_or_create(
                id=profile.id, defaults=fields, create_defaults={**fields, 'is_active': True},
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
