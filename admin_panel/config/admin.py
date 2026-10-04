"""Сайт админки: штатный Django admin плюс раздел «Рассылки» в каталоге.

Страницы рассылок (приложение campaigns) — не модели Django: своих таблиц у
них нет, данные живут в сервисе уведомлений и приходят по HTTP. Поэтому
Django сам не покажет их на главной админки и в боковом меню — раздел
добавляется в список приложений здесь, чтобы до рассылок можно было дойти
из интерфейса, а не набирая адрес.
"""

from django.contrib.admin import AdminSite
from django.http import HttpRequest
from django.urls import reverse

MAILINGS_LABEL = 'mailings'


class CinemaAdminSite(AdminSite):
    def get_app_list(self, request: HttpRequest, app_label: str | None = None) -> list[dict]:
        apps = super().get_app_list(request, app_label)
        user = request.user
        # Те же права, что у самих страниц рассылок (staff_member_required).
        if app_label in (None, MAILINGS_LABEL) and user.is_active and user.is_staff:
            apps.append(mailings_app())
        return apps


def mailings_app() -> dict:
    """Раздел «Рассылки» в формате, который ждут шаблоны админки."""
    campaigns = reverse('campaigns:campaigns')
    return {
        'name': 'Рассылки',
        'app_label': MAILINGS_LABEL,
        'app_url': campaigns,
        'has_module_perms': True,
        'models': [
            {
                'name': 'Рассылки',
                'object_name': 'Campaign',
                'admin_url': campaigns,
                'add_url': reverse('campaigns:campaign_create'),
                'view_only': False,
                'perms': {'add': True, 'change': True, 'delete': False, 'view': True},
            },
            {
                'name': 'Шаблоны писем',
                'object_name': 'Template',
                'admin_url': reverse('campaigns:templates'),
                'add_url': reverse('campaigns:template_create'),
                'view_only': False,
                'perms': {'add': True, 'change': True, 'delete': False, 'view': True},
            },
        ],
    }
