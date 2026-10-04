"""Клиент сервиса уведомлений и формы менеджера."""

import json
from http import HTTPStatus

import pytest
import requests

from campaigns.client import NotifyClient, NotifyRejectedError, NotifyUnavailableError
from campaigns.forms import CampaignForm, TemplateForm

TEMPLATE_PAYLOAD = {
    'code': 'welcome', 'name': 'Приветствие', 'channel': 'email',
    'subject': 'Привет', 'body': '<p>{{ first_name }}</p>', 'is_active': True,
}


class Response:
    """Ответ requests ровно в том объёме, который читает клиент."""

    def __init__(self, status: int, body: object = None) -> None:
        self.status_code = status
        self._body = body
        self.content = b'' if body is None else json.dumps(body).encode()

    def json(self) -> object:
        if self._body is None:
            raise ValueError('пустой ответ')
        return self._body


@pytest.fixture
def client() -> NotifyClient:
    return NotifyClient(base_url='http://notify-api:8000', service_token='service-token')


def test_service_token_is_sent(client: NotifyClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждый запрос несёт служебный секрет: своего пользователя у админки нет."""
    captured: dict = {}

    def fake_request(method, url, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return Response(HTTPStatus.OK, [])

    monkeypatch.setattr(requests, 'request', fake_request)

    client.list_templates()

    assert captured['headers']['X-Service-Token'] == 'service-token'


def test_request_id_is_forwarded(client: NotifyClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Идентификатор запроса едет дальше: по нему в журнале видно, кто запустил рассылку."""
    captured: dict = {}

    def fake_request(method, url, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return Response(HTTPStatus.OK, [])

    monkeypatch.setattr(requests, 'request', fake_request)

    client.list_campaigns()

    assert 'X-Request-Id' in captured['headers']


def test_network_failure_becomes_unavailable(client: NotifyClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой сети превращается в понятную ошибку, а не в исключение requests.

    Иначе подробности HTTP протекли бы во вьюхи, и менеджер увидел бы
    трейсбек вместо «сервис недоступен».
    """
    def fake_request(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise requests.ConnectionError('нет соединения')

    monkeypatch.setattr(requests, 'request', fake_request)

    with pytest.raises(NotifyUnavailableError):
        client.list_templates()


def test_server_error_becomes_unavailable(client: NotifyClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пятисотка сервиса — это его авария, а не ошибка менеджера."""
    monkeypatch.setattr(
        requests, 'request', lambda *a, **kw: Response(HTTPStatus.INTERNAL_SERVER_ERROR, {'detail': 'oops'}),
    )

    with pytest.raises(NotifyUnavailableError):
        client.list_templates()


def test_rejection_carries_reason_to_the_manager(client: NotifyClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Отказ по существу доносит причину: починить шаблон может только менеджер."""
    monkeypatch.setattr(
        requests, 'request',
        lambda *a, **kw: Response(HTTPStatus.BAD_REQUEST, {'code': 'template_invalid', 'detail': 'Unknown variables'}),
    )

    with pytest.raises(NotifyRejectedError) as error:
        client.create_template(TEMPLATE_PAYLOAD)

    assert 'Unknown variables' in str(error.value)


def test_empty_response_is_not_parsed(client: NotifyClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ 204 без тела не разбирается как JSON и не роняет удаление."""
    monkeypatch.setattr(requests, 'request', lambda *a, **kw: Response(HTTPStatus.NO_CONTENT))

    assert client.delete_template('welcome') is None


def test_template_form_builds_payload() -> None:
    """Форма шаблона собирает тело запроса к сервису."""
    form = TemplateForm(TEMPLATE_PAYLOAD)

    assert form.is_valid(), form.errors
    assert form.payload()['code'] == 'welcome'


def test_template_code_is_checked() -> None:
    """Код шаблона проверяется формой: по нему на шаблон ссылаются события."""
    form = TemplateForm({**TEMPLATE_PAYLOAD, 'code': 'Welcome Template'})

    assert not form.is_valid()
    assert 'code' in form.errors


def test_campaign_cannot_have_both_time_and_schedule() -> None:
    """Время запуска и расписание вместе не принимаются.

    Иначе непонятно, что считать периодом запуска, а от него зависит защита
    от повторов после простоя генератора.
    """
    form = CampaignForm(
        {
            'title': 'Подборка', 'template_code': 'weekly_digest', 'channel': 'email',
            'audience_kind': 'all', 'scheduled_at': '2026-10-02 18:00:00', 'cron': '0 18 * * 5',
        },
        template_codes=['weekly_digest'],
    )

    assert not form.is_valid()


def test_campaign_to_listed_viewers_requires_ids() -> None:
    """Рассылка «перечисленным зрителям» без списка не принимается."""
    form = CampaignForm(
        {'title': 'Подборка', 'template_code': 'weekly_digest', 'channel': 'email', 'audience_kind': 'users'},
        template_codes=['weekly_digest'],
    )

    assert not form.is_valid()


def test_campaign_context_must_be_json_object() -> None:
    """Данные для шаблона принимаются только объектом JSON."""
    form = CampaignForm(
        {
            'title': 'Подборка', 'template_code': 'weekly_digest', 'channel': 'email',
            'audience_kind': 'all', 'context': '[1, 2, 3]',
        },
        template_codes=['weekly_digest'],
    )

    assert not form.is_valid()
    assert 'context' in form.errors


def test_campaign_payload_has_audience_and_context() -> None:
    """Готовое тело запроса содержит адресатов и данные шаблона."""
    form = CampaignForm(
        {
            'title': 'Подборка', 'template_code': 'weekly_digest', 'channel': 'email',
            'audience_kind': 'all', 'context': '{"items": ["Матрица"]}',
        },
        template_codes=['weekly_digest'],
    )

    assert form.is_valid(), form.errors
    payload = form.payload()
    assert payload['audience'] == {'kind': 'all'}
    assert payload['context'] == {'items': ['Матрица']}
    # Ни времени, ни расписания — значит, рассылка уходит сразу.
    assert 'scheduled_at' not in payload
    assert 'cron' not in payload
