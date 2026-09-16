"""Выход из админки закрывает и сессию сотрудника в сервисе авторизации.

Вход оставляет сотруднику его сессию в сервисе авторизации: ею админка
перепроверяет доступ (users/recheck.py). Значит, кто-то должен её и закрыть —
иначе после выхода из админки она жила бы до истечения refresh-токена.
"""

import logging

from django.contrib.auth.signals import user_logged_out
from django.db import transaction
from django.dispatch import receiver

from users.auth_client import AuthServiceError, shared_client
from users.models import User

logger = logging.getLogger(__name__)


@receiver(user_logged_out)
def close_auth_session(sender, request, user, **kwargs) -> None:
    """Закрывает сессию вышедшего сотрудника в сервисе авторизации и забывает его токены."""
    if user is None or user.pk is None:
        return
    access_token = _forget_tokens(user.pk)
    if not access_token:
        # Аварийный локальный вход или сессия, уже закрытая перепроверкой.
        return
    try:
        shared_client().logout(access_token)
    except AuthServiceError as exc:
        # Сессия останется в сервисе авторизации до истечения refresh-токена;
        # в админку с ней уже не войти — токены забыты.
        logger.warning('Не удалось закрыть сессию сотрудника в сервисе авторизации: %s', exc)


def _forget_tokens(user_id) -> str:
    """Забирает токены сотрудника из базы, оставляя запись без них; '' — токенов не было."""
    with transaction.atomic():
        staff = User.objects.select_for_update().filter(pk=user_id).first()
        if staff is None or not staff.auth_access_token:
            return ''
        User.objects.filter(pk=user_id).update(auth_access_token='', auth_refresh_token='', auth_checked_at=None)
        return staff.auth_access_token
