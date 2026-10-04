"""Каталог админки: раздел «Рассылки» рядом с моделями, только для сотрудников."""

import pytest
from django.contrib import admin
from django.test import RequestFactory

from config.admin import CinemaAdminSite


class FakeUser:
    def __init__(self, staff: bool) -> None:
        self.is_active = True
        self.is_staff = staff
        self.is_superuser = staff

    def has_module_perms(self, app_label: str) -> bool:
        return self.is_staff

    def has_perm(self, perm: str, obj: object = None) -> bool:
        return self.is_staff


def app_list(staff: bool, app_label: str | None = None) -> list[dict]:
    request = RequestFactory().get('/admin/')
    request.user = FakeUser(staff)
    return admin.site.get_app_list(request, app_label)


def test_default_site_is_cinema_site():
    """Админка работает на своём сайте — иначе раздела рассылок в каталоге не будет."""
    assert isinstance(admin.site, CinemaAdminSite)


def test_mailings_section_with_links():
    """В каталоге есть «Рассылки» со ссылками на рассылки и шаблоны писем и кнопками «Добавить»."""
    mailings = next(app for app in app_list(staff=True) if app['app_label'] == 'mailings')

    links = {model['name']: (model['admin_url'], model['add_url']) for model in mailings['models']}
    assert links == {
        'Рассылки': ('/admin/mailings/', '/admin/mailings/new/'),
        'Шаблоны писем': ('/admin/mailings/templates/', '/admin/mailings/templates/new/'),
    }


@pytest.mark.parametrize('app_label', ['movies'])
def test_mailings_not_shown_on_other_app_page(app_label):
    """На странице другого приложения раздел рассылок не подмешивается."""
    assert all(app['app_label'] != 'mailings' for app in app_list(staff=True, app_label=app_label))


def test_mailings_hidden_from_non_staff():
    """Не сотруднику раздел не показывается: страницы рассылок его всё равно не пустят."""
    assert all(app['app_label'] != 'mailings' for app in app_list(staff=False))
