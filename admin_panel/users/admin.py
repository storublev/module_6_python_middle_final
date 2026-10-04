from django.contrib import admin
from django.http import HttpRequest

from users.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Сотрудники, входившие в админку.

    Записи заводит вход через сервис авторизации, поэтому здесь их не создают
    и не правят: логин и права приедут из сервиса при следующем входе.
    Единственное, что меняется тут, — признак «активен»: он закрывает доступ
    немедленно, не дожидаясь отзыва права в сервисе авторизации.
    """

    list_display = ('login', 'is_active', 'is_staff', 'is_superuser', 'last_login')
    list_filter = ('is_active', 'is_superuser')
    search_fields = ('login',)
    fields = ('login', 'is_active', 'is_staff', 'is_superuser', 'last_login', 'created_at', 'updated_at')
    readonly_fields = ('login', 'is_staff', 'is_superuser', 'last_login', 'created_at', 'updated_at')

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
