"""Ответы API: шаблоны, рассылки, личный кабинет, отписка и короткие ссылки."""

import uuid
from http import HTTPStatus

import jwt
import requests

from tests.functional.conftest import Viewer, send_event
from tests.functional.settings import settings
from tests.functional.utils import mailbox

VALID_TEMPLATE = {
    'code': 'placeholder',
    'name': 'Проверка',
    'channel': 'email',
    'subject': 'Привет, {{ first_name }}',
    'body': '<p>{{ full_name }}</p>',
    'is_active': True,
}


def test_health_is_open(api_url: str) -> None:
    """Проверка готовности отвечает без всяких секретов: её дёргает оркестратор."""
    response = requests.get(f'{api_url}/health', timeout=5)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {'status': 'ok'}


def test_openapi_is_published() -> None:
    """Сервис отдаёт спецификацию OpenAPI."""
    response = requests.get(f'{settings.service_url}/notify/api/openapi.json', timeout=5)

    assert response.status_code == HTTPStatus.OK
    assert '/notify/api/v1/events' in response.json()['paths']


def test_event_without_service_token_is_rejected(api_url: str) -> None:
    """Без служебного секрета событие не принимается."""
    response = send_event(api_url, headers={})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'service_token_invalid'


def test_event_with_wrong_service_token_is_rejected(api_url: str) -> None:
    """Чужой секрет не подходит."""
    response = send_event(api_url, headers={'X-Service-Token': 'not-the-right-one'})

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_event_with_bad_routing_key_is_rejected(api_url: str, service_headers: dict[str, str]) -> None:
    """Ключ маршрутизации проверяется по правилу урока.

    Формат `[сущность]-reporting.[версия].[событие]` — это контракт, и мусор в
    нём должен отклоняться на входе, а не всплывать в брокере.
    """
    response = send_event(
        api_url, service_headers,
        routing_key='просто событие',
        audience={'kind': 'all'},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_validation_error_does_not_echo_the_payload(
    api_url: str, service_headers: dict[str, str],
) -> None:
    """Ответ 422 не повторяет присланное значение.

    Телу письма и шаблону не место ни в ответе, ни в журналах прокси.
    """
    response = requests.post(
        f'{api_url}/templates',
        json={**VALID_TEMPLATE, 'code': 'НЕПРАВИЛЬНЫЙ', 'body': 'секретный текст письма'},
        headers=service_headers,
        timeout=10,
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'секретный текст письма' not in response.text
    assert all('input' not in item for item in response.json()['detail'])


def test_empty_audience_is_rejected(api_url: str, service_headers: dict[str, str]) -> None:
    """Событие без адресатов не принимается: писать некому."""
    response = send_event(api_url, service_headers, audience={'kind': 'users', 'user_ids': []})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_template_crud(api_url: str, service_headers: dict[str, str]) -> None:
    """Шаблон создаётся, читается, правится и удаляется."""
    code = f'crud_{uuid.uuid4().hex[:10]}'
    payload = {**VALID_TEMPLATE, 'code': code}

    created = requests.post(f'{api_url}/templates', json=payload, headers=service_headers, timeout=10)
    assert created.status_code == HTTPStatus.CREATED
    assert created.json()['version'] == 1

    read = requests.get(f'{api_url}/templates/{code}', headers=service_headers, timeout=10)
    assert read.status_code == HTTPStatus.OK

    updated = requests.put(
        f'{api_url}/templates/{code}', json={**payload, 'subject': 'Здравствуйте'},
        headers=service_headers, timeout=10,
    )
    assert updated.status_code == HTTPStatus.OK
    # Правка повышает версию: по ней рассылка, начатая со старым текстом,
    # досылается старым текстом.
    assert updated.json()['version'] == 2

    listed = requests.get(f'{api_url}/templates', headers=service_headers, timeout=10)
    assert code in {item['code'] for item in listed.json()}

    deleted = requests.delete(f'{api_url}/templates/{code}', headers=service_headers, timeout=10)
    assert deleted.status_code == HTTPStatus.NO_CONTENT
    assert requests.get(
        f'{api_url}/templates/{code}', headers=service_headers, timeout=10,
    ).status_code == HTTPStatus.NOT_FOUND


def test_template_with_unknown_variable_is_rejected(api_url: str, service_headers: dict[str, str]) -> None:
    """Шаблон с переменной, которой у нас нет, не сохраняется."""
    response = requests.post(
        f'{api_url}/templates',
        json={**VALID_TEMPLATE, 'code': f'bad_{uuid.uuid4().hex[:8]}', 'body': '{{ password_hash }}'},
        headers=service_headers,
        timeout=10,
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()['code'] == 'template_invalid'


def test_duplicate_template_code_is_rejected(api_url: str, service_headers: dict[str, str]) -> None:
    """Второй шаблон с тем же кодом не создаётся."""
    code = f'dup_{uuid.uuid4().hex[:10]}'
    payload = {**VALID_TEMPLATE, 'code': code}
    requests.post(f'{api_url}/templates', json=payload, headers=service_headers, timeout=10)

    second = requests.post(f'{api_url}/templates', json=payload, headers=service_headers, timeout=10)

    assert second.status_code == HTTPStatus.CONFLICT
    requests.delete(f'{api_url}/templates/{code}', headers=service_headers, timeout=10)


def test_template_preview_does_not_save(api_url: str, service_headers: dict[str, str]) -> None:
    """Предпросмотр показывает письмо и ничего не сохраняет."""
    code = f'preview_{uuid.uuid4().hex[:8]}'

    preview = requests.post(
        f'{api_url}/templates/preview', json={**VALID_TEMPLATE, 'code': code},
        headers=service_headers, timeout=10,
    )

    assert preview.status_code == HTTPStatus.OK
    assert preview.json()['subject'] == 'Привет, Томас'
    assert requests.get(
        f'{api_url}/templates/{code}', headers=service_headers, timeout=10,
    ).status_code == HTTPStatus.NOT_FOUND


def test_campaign_sends_to_all(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """Рассылка менеджера без расписания уходит сразу — это кнопка «Отправить»."""
    created = requests.post(
        f'{api_url}/campaigns',
        json={
            'title': 'Проверочная рассылка',
            'template_code': template,
            'channel': 'email',
            'audience': {'kind': 'users', 'user_ids': [viewer.user_id]},
            'context': {'film_title': 'Матрица'},
        },
        headers=service_headers,
        timeout=10,
    )

    assert created.status_code == HTTPStatus.CREATED
    letter = mailbox.wait_for_letter(viewer.email)
    assert 'Матрица' in letter.html


def test_campaign_with_unknown_template_is_rejected(api_url: str, service_headers: dict[str, str]) -> None:
    """Рассылку по несуществующему шаблону создать нельзя."""
    response = requests.post(
        f'{api_url}/campaigns',
        json={
            'title': 'Пустая', 'template_code': 'no_such_template',
            'channel': 'email', 'audience': {'kind': 'all'},
        },
        headers=service_headers,
        timeout=10,
    )

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_scheduled_campaign_can_be_cancelled(api_url: str, service_headers: dict[str, str], template: str) -> None:
    """Отложенную рассылку можно отменить, и запустить её после этого нельзя."""
    created = requests.post(
        f'{api_url}/campaigns',
        json={
            'title': 'Отложенная', 'template_code': template, 'channel': 'email',
            'audience': {'kind': 'all'}, 'scheduled_at': '2030-01-01T10:00:00+00:00',
        },
        headers=service_headers,
        timeout=10,
    )
    campaign_id = created.json()['id']

    cancelled = requests.post(
        f'{api_url}/campaigns/{campaign_id}/cancel', headers=service_headers, timeout=10,
    )
    run = requests.post(f'{api_url}/campaigns/{campaign_id}/run', headers=service_headers, timeout=10)

    assert cancelled.json()['status'] == 'cancelled'
    assert run.status_code == HTTPStatus.BAD_REQUEST
    assert run.json()['code'] == 'campaign_not_runnable'


def test_campaign_of_unknown_id_is_not_found(api_url: str, service_headers: dict[str, str]) -> None:
    """Несуществующая рассылка — это 404."""
    response = requests.post(
        f'{api_url}/campaigns/{uuid.uuid4()}/run', headers=service_headers, timeout=10,
    )

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_viewer_sees_own_notifications(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """Зритель видит свои уведомления в личном кабинете."""
    send_event(
        api_url, service_headers,
        template_code=template,
        audience={'kind': 'users', 'user_ids': [viewer.user_id]},
        context={'film_title': 'Матрица'},
    )
    mailbox.wait_for_letter(viewer.email)

    response = requests.get(
        f'{api_url}/me/notifications', headers=viewer.auth_headers, timeout=10,
    )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body['total'] >= 1
    assert body['items'][0]['status'] == 'sent'


def test_notifications_require_token(api_url: str) -> None:
    """Личный кабинет без токена не отдаётся."""
    response = requests.get(f'{api_url}/me/notifications', timeout=10)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'not_authenticated'


def test_expired_token_is_rejected(api_url: str, viewer: Viewer) -> None:
    """Просроченный токен не пускает в личный кабинет."""
    expired = jwt.encode(
        {
            'sub': viewer.user_id, 'sid': str(uuid.uuid4()), 'jti': str(uuid.uuid4()),
            'type': 'access', 'iat': 0, 'exp': 1,
        },
        settings.jwt_secret_key,
        algorithm='HS256',
    )

    response = requests.get(
        f'{api_url}/me/notifications', headers={'Authorization': f'Bearer {expired}'}, timeout=10,
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_expired'


def test_viewer_can_turn_notifications_off(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """Выключенный тип уведомлений больше не приходит.

    Это прямое требование задания: «должна быть возможность настройки
    уведомлений пользователем, в том числе отключение уведомлений».
    """
    off = requests.put(
        f'{api_url}/me/subscriptions',
        json={'template_code': template, 'channel': 'email', 'enabled': False},
        headers=viewer.auth_headers,
        timeout=10,
    )
    assert off.status_code == HTTPStatus.OK

    send_event(
        api_url, service_headers,
        template_code=template,
        audience={'kind': 'users', 'user_ids': [viewer.user_id]},
        context={'film_title': 'Матрица'},
    )

    mailbox.expect_no_letter(viewer.email, within=8)


def test_subscriptions_are_listed(api_url: str, viewer: Viewer, template: str) -> None:
    """Зритель видит свои настройки уведомлений."""
    requests.put(
        f'{api_url}/me/subscriptions',
        json={'template_code': template, 'channel': 'email', 'enabled': False},
        headers=viewer.auth_headers,
        timeout=10,
    )

    response = requests.get(f'{api_url}/me/subscriptions', headers=viewer.auth_headers, timeout=10)

    assert response.status_code == HTTPStatus.OK
    assert any(item['template_code'] == template and not item['enabled'] for item in response.json()['items'])


def test_unsubscribe_all_stops_everything(api_url: str, viewer: Viewer, template: str) -> None:
    """Кнопка «отписаться от всего» выключает все настройки зрителя."""
    requests.put(
        f'{api_url}/me/subscriptions',
        json={'template_code': template, 'channel': 'email', 'enabled': True},
        headers=viewer.auth_headers,
        timeout=10,
    )

    response = requests.delete(f'{api_url}/me/subscriptions', headers=viewer.auth_headers, timeout=10)

    assert response.status_code == HTTPStatus.NO_CONTENT
    listed = requests.get(f'{api_url}/me/subscriptions', headers=viewer.auth_headers, timeout=10)
    assert listed.json()['unsubscribed_all'] is True
    assert all(not item['enabled'] for item in listed.json()['items'])


def test_unsubscribe_link_rejects_wrong_signature(api_url: str, viewer: Viewer) -> None:
    """Отписаться по чужой ссылке нельзя."""
    response = requests.get(
        f'{api_url}/unsubscribe', params={'user_id': viewer.user_id, 'token': 'подделка'}, timeout=10,
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()['code'] == 'token_invalid'


def test_short_link_redirects(api_url: str, service_headers: dict[str, str]) -> None:
    """Короткая ссылка ведёт на исходный адрес кодом 302."""
    created = requests.post(
        f'{api_url}/links', json={'target_url': 'https://practix.local/films'},
        headers=service_headers, timeout=10,
    )
    assert created.status_code == HTTPStatus.CREATED

    key = created.json()['key']
    response = requests.get(f'{settings.service_url}/s/{key}', allow_redirects=False, timeout=10)

    assert response.status_code == HTTPStatus.FOUND
    assert response.headers['Location'] == 'https://practix.local/films'


def test_expired_short_link_is_not_found(api_url: str, service_headers: dict[str, str]) -> None:
    """Просроченная ссылка отдаёт 404, как требует задание урока."""
    created = requests.post(
        f'{api_url}/links', json={'target_url': 'https://practix.local/', 'ttl_seconds': 1},
        headers=service_headers, timeout=10,
    )
    key = created.json()['key']

    import time

    time.sleep(1.5)
    response = requests.get(f'{settings.service_url}/s/{key}', allow_redirects=False, timeout=10)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['code'] == 'link_not_found'


def test_unknown_short_link_is_not_found() -> None:
    """Несуществующий ключ — тоже 404, а не редирект в никуда."""
    response = requests.get(f'{settings.service_url}/s/nosuch', allow_redirects=False, timeout=10)

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_link_creation_requires_service_token(api_url: str) -> None:
    """Сокращать ссылки может только сервис, а не любой прохожий."""
    response = requests.post(
        f'{api_url}/links', json={'target_url': 'https://practix.local/'}, timeout=10,
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_confirmation_by_user_id_alone_is_impossible(api_url: str, viewer: Viewer) -> None:
    """Зная только идентификатор зрителя, адрес не подтвердить.

    Ровно это и позволял прежний эндпоинт: `?user_id=…&redirectUrl=…`.
    """
    by_id = requests.get(
        f'{api_url}/confirm-email',
        params={'user_id': viewer.user_id, 'redirectUrl': 'https://example.com/'},
        allow_redirects=False, timeout=10,
    )
    forged = requests.get(
        f'{api_url}/confirm-email',
        params={'token': viewer.user_id, 'redirectUrl': 'https://example.com/'},
        allow_redirects=False, timeout=10,
    )

    assert by_id.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert forged.status_code == HTTPStatus.NOT_FOUND
    assert forged.json()['code'] == 'confirmation_link_invalid'
    status = requests.get(f'{api_url}/me/email-confirmation', headers=viewer.auth_headers, timeout=10)
    assert status.json() == {'email': None, 'confirmed_at': None}


def test_email_confirmation_requires_token(api_url: str) -> None:
    """Статус подтверждения виден только самому зрителю."""
    response = requests.get(f'{api_url}/me/email-confirmation', timeout=10)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
