"""Маршруты админки.

Своего API здесь нет: каталог наружу отдаёт сервис контента (/api/v1/…),
который берёт данные из Elasticsearch, наполняемого ETL из этой же базы.

Под тем же /admin/ живут страницы рассылок (`campaigns`): менеджер ведёт
шаблоны писем и запускает рассылки там же, где редакторы ведут каталог, и
тем же входом через сервис авторизации.
"""

from django.contrib import admin
from django.urls import include, path

admin.site.site_header = 'Онлайн-кинотеатр'
admin.site.site_title = 'Онлайн-кинотеатр'
admin.site.index_title = 'Управление каталогом'

urlpatterns = [
    # Страницы рассылок объявлены до админки: иначе её catch-all перехватил бы
    # /admin/mailings/ и отдал бы 404 вместо страницы менеджера.
    path('admin/mailings/', include('campaigns.urls')),
    path('admin/', admin.site.urls),
]
