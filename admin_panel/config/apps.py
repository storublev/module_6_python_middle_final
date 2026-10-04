from django.contrib.admin.apps import AdminConfig


class CinemaAdminConfig(AdminConfig):
    """Штатная админка Django со своим сайтом: в каталоге есть раздел «Рассылки»."""

    default_site = 'config.admin.CinemaAdminSite'
