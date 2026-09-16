"""Сотрудник админки — отражение учётной записи сервиса авторизации.

Пароли сотрудников хранит сервис авторизации, а не админка: иначе у человека
было бы два пароля и два места, где его нужно отзывать. Локальная запись
нужна лишь для того, чтобы Django связал сессию с пользователем и показал имя
в интерфейсе, поэтому её идентификатор совпадает с идентификатором в сервисе
авторизации, а пароль по умолчанию непригоден для входа.

Исключение — аварийный суперпользователь, созданный `manage.py
createsuperuser`: у него пароль есть, и он входит через ModelBackend, когда
сервис авторизации недоступен. Его логин не должен совпадать с логином из
сервиса авторизации, иначе записи столкнутся на уникальном логине.

Вместе с записью хранится сессия сотрудника в сервисе авторизации: ею админка
перепроверяет его доступ, пока он работает, и закрывает её при выходе.
"""

import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _

LOGIN_MAX_LENGTH = 64


class UserManager(BaseUserManager):
    def create_user(self, login: str, password: str | None = None, **extra_fields) -> 'User':
        if not login:
            raise ValueError('Login is required')
        user = self.model(login=login, **extra_fields)
        if password:
            user.set_password(password)
        else:
            # Пароля нет — войти по паролю нельзя, только через сервис авторизации.
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, login: str, password: str, **extra_fields) -> 'User':
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(login, password, **extra_fields)


class User(AbstractBaseUser):
    # Тот же идентификатор, что у учётной записи в сервисе авторизации:
    # по нему записи связаны, а переименование логина ничего не ломает.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    login = models.CharField(_('login'), max_length=LOGIN_MAX_LENGTH, unique=True)
    is_active = models.BooleanField(_('active'), default=True)
    is_staff = models.BooleanField(_('staff status'), default=True)
    is_superuser = models.BooleanField(_('superuser status'), default=False)
    # Токены сессии сотрудника в сервисе авторизации: ими админка
    # перепроверяет его доступ, пока он работает (users/recheck.py). Хранятся
    # у сотрудника, а не в сессии Django, чтобы проверку и обновление
    # одноразового refresh-токена можно было выстроить в очередь блокировкой
    # строки: параллельные запросы одной страницы иначе гасят сессию друг другу.
    auth_access_token = models.TextField(_('access token'), blank=True, default='')
    auth_refresh_token = models.TextField(_('refresh token'), blank=True, default='')
    auth_checked_at = models.DateTimeField(_('access checked'), null=True, blank=True)
    created_at = models.DateTimeField(_('created'), auto_now_add=True)
    updated_at = models.DateTimeField(_('modified'), auto_now=True)

    USERNAME_FIELD = 'login'
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    def __str__(self) -> str:
        return self.login

    def has_perm(self, perm: str, obj=None) -> bool:
        """Право сотрудника в админке.

        Права внутри админки не дробятся: кого сервис авторизации пустил
        (право admin.access или суперпользователь), тот ведёт весь каталог.
        Разграничение доступа живёт в сервисе авторизации — в одном месте,
        а не половиной в его ролях и половиной в таблицах Django.
        """
        return self.is_active and self.is_staff

    def has_module_perms(self, app_label: str) -> bool:
        return self.is_active and self.is_staff

    class Meta:
        db_table = 'admin_user'
        verbose_name = _('staff member')
        verbose_name_plural = _('staff members')
        ordering = ('login',)
