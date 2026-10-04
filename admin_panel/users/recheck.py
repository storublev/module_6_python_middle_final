"""Перепроверка доступа сотрудника, пока он работает в админке.

Права проверяются в сервисе авторизации при входе, но вход — это один момент
времени, а сессия Django живёт долго. Если у сотрудника отобрали право
`admin.access`, уволили его или закрыли его сессии в сервисе авторизации
(сменой пароля или выходом со всех устройств), он продолжал бы работать через
уже открытую сессию админки. Поэтому вход не закрывает сессию в сервисе
авторизации, а оставляет её сотруднику: middleware время от времени
предъявляет её токен и спрашивает право заново.

Проверка идёт не на каждый запрос, а не чаще раза в `auth_recheck_interval`:
страница админки тянет за собой несколько запросов, и каждый ходил бы в
сервис авторизации. Отзыв права закрывает админку в пределах этого интервала.

Одновременные запросы одного сотрудника выстраиваются в очередь блокировкой
его строки: refresh-токен одноразовый, и два запроса, обновляющие пару
параллельно, закрыли бы сессию друг другу. Блокировка держится и на время
запроса к сервису авторизации — этим платим за то, что перепроверка не
гасит сама себя; ждут только запросы того же сотрудника и не дольше таймаутов
клиента.

Недоступность сервиса авторизации сотрудника сразу не выгоняет: пока он
недоступен, действует последняя удачная проверка, но не дольше
`auth_unavailable_grace`. Иначе перезапуск сервиса авторизации обрывал бы
работу всем сразу, а вечное доверие оставило бы отозванный доступ навсегда.
"""

import logging
from collections.abc import Callable
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import logout
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from users.auth_client import (
    AuthClient,
    AuthProfile,
    AuthServiceError,
    SessionExpiredError,
    Tokens,
    build_client,
)
from users.models import User

logger = logging.getLogger(__name__)


class AccessRecheckMiddleware:
    """Закрывает сессию админки, когда доступ сотрудника в сервисе авторизации кончился."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse], client: AuthClient | None = None):
        self.get_response = get_response
        # Клиент с его прерывателем — один на процесс, как у бэкенда входа.
        self._client = client or build_client()
        config = settings.APP_SETTINGS
        self._permission = config.auth_admin_permission
        self._interval = timedelta(seconds=config.auth_recheck_interval)
        self._grace = timedelta(seconds=config.auth_unavailable_grace)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and not self.allowed(user.pk):
            # Дальше запрос идёт уже анонимным: админка сама отправит на форму входа.
            logout(request)
        return self.get_response(request)

    def allowed(self, user_id) -> bool:
        """Можно ли сотруднику продолжать работу в админке."""
        with transaction.atomic():
            staff = User.objects.select_for_update().filter(pk=user_id).first()
            if staff is None or not staff.is_active:
                return False
            if not staff.auth_refresh_token:
                # Аварийный локальный суперпользователь: сервис авторизации
                # его не заводил и права на него не имеет.
                return True
            if staff.auth_checked_at and timezone.now() - staff.auth_checked_at < self._interval:
                return True
            return self._recheck(staff)

    def _recheck(self, staff: User) -> bool:
        """Спрашивает сервис авторизации, остался ли у сотрудника доступ, и запоминает ответ."""
        try:
            tokens, profile, allowed = self._ask(staff)
        except SessionExpiredError as exc:
            logger.info('Сессия сотрудника %s в сервисе авторизации закрыта (%s), выходим из админки',
                        staff.login, exc)
            self._close(staff)
            return False
        except AuthServiceError as exc:
            return self._tolerate(staff, exc)
        if not allowed:
            logger.info('У сотрудника %s больше нет права на админку, сессия закрыта', staff.login)
            self._client.logout(tokens.access)
            self._close(staff)
            return False
        User.objects.filter(pk=staff.pk).update(
            login=profile.login,
            is_superuser=profile.is_superuser,
            auth_access_token=tokens.access,
            auth_refresh_token=tokens.refresh,
            auth_checked_at=timezone.now(),
        )
        return True

    def _ask(self, staff: User) -> tuple[Tokens, AuthProfile, bool]:
        """Читает данные и право по действующему токену, обновив пару, если access-токен истёк."""
        tokens = Tokens(access=staff.auth_access_token, refresh=staff.auth_refresh_token)
        try:
            return (tokens, *self._verify(tokens.access))
        except SessionExpiredError:
            # access-токен живёт минуты: истёк — берём новую пару по refresh.
            # Если закрыта сама сессия, refresh тоже ответит отказом.
            tokens = self._client.refresh(tokens.refresh)
            return (tokens, *self._verify(tokens.access))

    def _verify(self, access_token: str) -> tuple[AuthProfile, bool]:
        profile = self._client.get_profile(access_token)
        # Право читается по базе (fresh), а не по кешу: отозванное должно
        # закрывать админку сразу, а не через время жизни кеша.
        return profile, profile.is_superuser or self._client.check_permission(access_token, self._permission)

    def _tolerate(self, staff: User, error: AuthServiceError) -> bool:
        """Сервис авторизации не ответил: доверяем прошлой проверке, но не дольше grace-периода."""
        checked_at = staff.auth_checked_at
        if checked_at is None or timezone.now() - checked_at > self._grace:
            logger.warning('Сервис авторизации недоступен дольше допустимого, сотрудник %s выходит: %s',
                           staff.login, error)
            return False
        logger.warning('Сервис авторизации недоступен, доступ сотрудника %s не перепроверен: %s',
                       staff.login, error)
        return True

    @staticmethod
    def _close(staff: User) -> None:
        """Забывает сессию сотрудника в сервисе авторизации: предъявлять больше нечего."""
        User.objects.filter(pk=staff.pk).update(auth_access_token='', auth_refresh_token='', auth_checked_at=None)
