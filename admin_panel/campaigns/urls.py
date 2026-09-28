"""Маршруты страниц рассылок. Живут под /admin/, как часть админки."""

from django.urls import path

from campaigns import views

app_name = 'campaigns'

urlpatterns = [
    path('templates/', views.template_list, name='templates'),
    path('templates/new/', views.template_create, name='template_create'),
    path('templates/<str:code>/', views.template_edit, name='template_edit'),
    path('', views.campaign_list, name='campaigns'),
    path('new/', views.campaign_create, name='campaign_create'),
    path('<str:campaign_id>/run/', views.campaign_run, name='campaign_run'),
    path('<str:campaign_id>/cancel/', views.campaign_cancel, name='campaign_cancel'),
]
