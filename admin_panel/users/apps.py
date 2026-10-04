from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class UsersConfig(AppConfig):
    name = 'users'
    verbose_name = _('staff')

    def ready(self) -> None:
        # Обработчик выхода подключается импортом модуля — до первого запроса.
        from users import signals  # noqa: F401
