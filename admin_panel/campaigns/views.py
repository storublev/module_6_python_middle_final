"""Страницы менеджера: шаблоны писем и рассылки.

Это и есть административная панель из чек-листа задания. Она не хранит ничего
своего: шаблоны и рассылки живут в сервисе уведомлений, а здесь только формы и
кнопки. Страницы оформлены шаблонами админки Django, поэтому выглядят её
частью и требуют того же входа — через сервис авторизации.
"""

import logging
from typing import Any

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from campaigns.client import NotifyClient, NotifyRejectedError, NotifyServiceError, NotifyUnavailableError
from campaigns.forms import CampaignForm, TemplateForm

logger = logging.getLogger(__name__)


def client() -> NotifyClient:
    config = django_settings.APP_SETTINGS
    return NotifyClient(
        base_url=config.notify_api_url,
        service_token=config.notify_service_token.get_secret_value(),
        connect_timeout=config.notify_connect_timeout,
        read_timeout=config.notify_read_timeout,
    )


@staff_member_required
def template_list(request: HttpRequest) -> HttpResponse:
    """Все шаблоны писем."""
    try:
        templates = client().list_templates()
    except NotifyServiceError as error:
        return _unavailable(request, error, 'campaigns/templates.html', {'templates': []})
    return render(request, 'campaigns/templates.html', {'templates': templates})


@staff_member_required
def template_create(request: HttpRequest) -> HttpResponse:
    """Создание шаблона с обязательным предпросмотром.

    Кнопка «Посмотреть» собирает письмо на тестовых данных и ничего не
    сохраняет: менеджер видит результат до того, как письмо уйдёт зрителям.
    """
    form = TemplateForm(request.POST or None)
    preview: dict[str, Any] | None = None
    if request.method == 'POST' and form.is_valid():
        action = request.POST.get('action', 'save')
        try:
            if action == 'preview':
                preview = client().preview_template(form.payload())
            else:
                created = client().create_template(form.payload())
                messages.success(request, _('Template %(code)s saved') % {'code': created['code']})
                return redirect(reverse('campaigns:templates'))
        except NotifyRejectedError as error:
            # Ошибка по существу: это менеджер ошибся в шаблоне, и текст
            # ошибки из сервиса — самое полезное, что можно ему показать.
            form.add_error(None, str(error))
        except NotifyUnavailableError as error:
            _unavailable_message(request, error)
    return render(request, 'campaigns/template_form.html', {'form': form, 'preview': preview})


@staff_member_required
def template_edit(request: HttpRequest, code: str) -> HttpResponse:
    """Правка шаблона. Каждое сохранение повышает его версию."""
    api = client()
    if request.method == 'POST':
        form = TemplateForm(request.POST)
        preview = None
        if form.is_valid():
            try:
                if request.POST.get('action') == 'preview':
                    preview = api.preview_template(form.payload())
                else:
                    api.update_template(code, form.payload())
                    messages.success(request, _('Template %(code)s updated') % {'code': code})
                    return redirect(reverse('campaigns:templates'))
            except NotifyRejectedError as error:
                form.add_error(None, str(error))
            except NotifyUnavailableError as error:
                _unavailable_message(request, error)
        return render(request, 'campaigns/template_form.html', {'form': form, 'preview': preview, 'code': code})

    try:
        current = api.get_template(code)
    except NotifyServiceError as error:
        return _unavailable(request, error, 'campaigns/templates.html', {'templates': []})
    return render(
        request, 'campaigns/template_form.html', {'form': TemplateForm(initial=current), 'code': code},
    )


@staff_member_required
def campaign_list(request: HttpRequest) -> HttpResponse:
    """Все рассылки и кнопки запуска и отмены."""
    try:
        campaigns = client().list_campaigns()
    except NotifyServiceError as error:
        return _unavailable(request, error, 'campaigns/campaigns.html', {'campaigns': []})
    return render(request, 'campaigns/campaigns.html', {'campaigns': campaigns})


@staff_member_required
def campaign_create(request: HttpRequest) -> HttpResponse:
    """Создание рассылки: сразу, отложенно или повторяемо."""
    api = client()
    try:
        codes = [item['code'] for item in api.list_templates() if item['is_active']]
    except NotifyServiceError as error:
        return _unavailable(request, error, 'campaigns/campaigns.html', {'campaigns': []})

    form = CampaignForm(request.POST or None, template_codes=codes)
    if request.method == 'POST' and form.is_valid():
        try:
            created = api.create_campaign(form.payload())
            messages.success(request, _('Mailing "%(title)s" created') % {'title': created['title']})
            return redirect(reverse('campaigns:campaigns'))
        except NotifyRejectedError as error:
            form.add_error(None, str(error))
        except NotifyUnavailableError as error:
            _unavailable_message(request, error)
    return render(request, 'campaigns/campaign_form.html', {'form': form})


@staff_member_required
def campaign_run(request: HttpRequest, campaign_id: str) -> HttpResponse:
    """Кнопка «Запустить сейчас»."""
    return _act(request, campaign_id, action='run')


@staff_member_required
def campaign_cancel(request: HttpRequest, campaign_id: str) -> HttpResponse:
    """Кнопка «Отменить»."""
    return _act(request, campaign_id, action='cancel')


def _act(request: HttpRequest, campaign_id: str, action: str) -> HttpResponse:
    api = client()
    try:
        if action == 'run':
            api.run_campaign(campaign_id)
            messages.success(request, _('Mailing started'))
        else:
            api.cancel_campaign(campaign_id)
            messages.success(request, _('Mailing cancelled'))
    except NotifyRejectedError as error:
        messages.error(request, str(error))
    except NotifyUnavailableError as error:
        _unavailable_message(request, error)
    return redirect(reverse('campaigns:campaigns'))


def _unavailable(request: HttpRequest, error: Exception, template: str, context: dict) -> HttpResponse:
    _unavailable_message(request, error)
    return render(request, template, context)


def _unavailable_message(request: HttpRequest, error: Exception) -> None:
    logger.warning('Сервис уведомлений недоступен: %s', error)
    messages.error(request, _('Notification service is unavailable, try again later'))
