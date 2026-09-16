"""Маршруты админки.

Своего API здесь нет: каталог наружу отдаёт сервис контента (/api/v1/…),
который берёт данные из Elasticsearch, наполняемого ETL из этой же базы.
"""

from django.contrib import admin
from django.urls import path

admin.site.site_header = 'Онлайн-кинотеатр'
admin.site.site_title = 'Онлайн-кинотеатр'
admin.site.index_title = 'Управление каталогом'

urlpatterns = [
    path('admin/', admin.site.urls),
]
