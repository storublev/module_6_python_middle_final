"""Формы менеджера: шаблон письма и рассылка."""

import json

from django import forms
from django.utils.translation import gettext_lazy as _

CHANNELS = (('email', _('email')), ('websocket', _('website notification')))
AUDIENCE_KINDS = (
    ('all', _('all viewers')),
    ('users', _('listed viewers')),
)


class TemplateForm(forms.Form):
    """Шаблон письма.

    Проверку самого шаблона делает сервис уведомлений: там же, где он потом
    собирается. Дублировать её здесь значило бы разойтись при первой правке
    списка разрешённых переменных.
    """

    code = forms.RegexField(
        label=_('code'), regex=r'^[a-z][a-z0-9_]*$', max_length=64,
        help_text=_('Latin lowercase, digits and underscore. Events refer to the template by this code.'),
    )
    name = forms.CharField(label=_('name'), max_length=128)
    channel = forms.ChoiceField(label=_('channel'), choices=CHANNELS, initial='email')
    subject = forms.CharField(label=_('subject'), max_length=255)
    body = forms.CharField(
        label=_('body'), widget=forms.Textarea(attrs={'rows': 18, 'cols': 100}),
        help_text=_('HTML with Jinja2 variables, for example {{ first_name }}.'),
    )
    is_active = forms.BooleanField(label=_('active'), required=False, initial=True)

    def payload(self) -> dict:
        return {
            'code': self.cleaned_data['code'],
            'name': self.cleaned_data['name'],
            'channel': self.cleaned_data['channel'],
            'subject': self.cleaned_data['subject'],
            'body': self.cleaned_data['body'],
            'is_active': self.cleaned_data['is_active'],
        }


class CampaignForm(forms.Form):
    """Рассылка: сразу, отложенно или повторяемо."""

    title = forms.CharField(label=_('title'), max_length=255)
    template_code = forms.ChoiceField(label=_('template'), choices=())
    channel = forms.ChoiceField(label=_('channel'), choices=CHANNELS, initial='email')
    audience_kind = forms.ChoiceField(label=_('audience'), choices=AUDIENCE_KINDS, initial='all')
    user_ids = forms.CharField(
        label=_('viewer ids'), required=False, widget=forms.Textarea(attrs={'rows': 4}),
        help_text=_('One identifier per line. Only for "listed viewers".'),
    )
    scheduled_at = forms.DateTimeField(
        label=_('send at'), required=False,
        help_text=_('Leave empty to send right away. Mutually exclusive with the schedule.'),
    )
    cron = forms.CharField(
        label=_('schedule'), required=False, max_length=128,
        help_text=_('Five cron fields, for example "0 18 * * 5" — every Friday at 18:00.'),
    )
    context = forms.CharField(
        label=_('template data'), required=False, widget=forms.Textarea(attrs={'rows': 4}),
        help_text=_('JSON object substituted into the template, for example {"items": ["The Matrix"]}.'),
    )

    def __init__(self, *args: object, template_codes: list[str] | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.fields['template_code'].choices = [(code, code) for code in (template_codes or [])]

    def clean_context(self) -> dict:
        raw = self.cleaned_data.get('context', '').strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError as error:
            raise forms.ValidationError(_('Template data must be valid JSON')) from error
        if not isinstance(parsed, dict):
            raise forms.ValidationError(_('Template data must be a JSON object'))
        return parsed

    def clean(self) -> dict:
        cleaned = super().clean()
        if cleaned.get('scheduled_at') and cleaned.get('cron'):
            # Оба сразу — это неоднозначность: непонятно, что считать
            # периодом запуска, а от него зависит защита от повторов.
            raise forms.ValidationError(_('Choose either a single send time or a repeating schedule'))
        if cleaned.get('audience_kind') == 'users' and not _ids_of(cleaned.get('user_ids', '')):
            raise forms.ValidationError(_('List at least one viewer identifier'))
        return cleaned

    def payload(self) -> dict:
        audience: dict = {'kind': self.cleaned_data['audience_kind']}
        if audience['kind'] == 'users':
            audience['user_ids'] = _ids_of(self.cleaned_data['user_ids'])
        body: dict = {
            'title': self.cleaned_data['title'],
            'template_code': self.cleaned_data['template_code'],
            'channel': self.cleaned_data['channel'],
            'audience': audience,
            'context': self.cleaned_data['context'],
        }
        if self.cleaned_data.get('scheduled_at'):
            body['scheduled_at'] = self.cleaned_data['scheduled_at'].isoformat()
        if self.cleaned_data.get('cron'):
            body['cron'] = self.cleaned_data['cron']
        return body


def _ids_of(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]
