"""Приём события и весь путь до письма в почтовом ящике."""

import uuid
from http import HTTPStatus

import requests

from tests.functional.conftest import PASSWORD, Viewer, register, send_event
from tests.functional.settings import settings
from tests.functional.utils import mailbox


def test_event_turns_into_personal_letter(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """Событие доезжает до письма, и письмо персонализировано.

    Это сквозная проверка всего конвейера: API → очередь → планировщик →
    сборщик → отправитель → почтовый сервер. По пути воркер сам сходил в
    сервис авторизации за адресом и именем — в событии их не было.
    """
    response = send_event(
        api_url, service_headers,
        template_code=template,
        audience={'kind': 'users', 'user_ids': [viewer.user_id]},
        context={'film_title': 'Матрица'},
    )

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json()['accepted'] is True

    letter = mailbox.wait_for_letter(viewer.email)
    assert letter.subject == 'Здравствуйте, Томас'
    assert 'Томас Андерсон' in letter.html
    assert 'Матрица' in letter.html


def test_repeated_event_id_does_not_send_second_letter(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """Повтор события с тем же event_id не отправляет второе письмо.

    Отправитель события, потерявший ответ, обязан повторить запрос — и зритель
    не должен получить два письма.
    """
    event_id = str(uuid.uuid4())
    payload = {
        'event_id': event_id,
        'template_code': template,
        'audience': {'kind': 'users', 'user_ids': [viewer.user_id]},
        'context': {'film_title': 'Матрица'},
    }

    first = send_event(api_url, service_headers, **payload)
    mailbox.wait_for_letter(viewer.email)
    second = send_event(api_url, service_headers, **payload)

    assert first.json()['accepted'] is True
    assert second.json()['accepted'] is False
    # Ждём, чтобы дубль успел бы дойти, если бы защита не работала.
    mailbox.expect_no_letter('nobody@example.com', within=5)
    assert len(mailbox.letters_for(viewer.email)) == 1


def test_letter_is_not_repeated_for_the_same_content_version(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """О той же версии данных второй раз не пишем даже новым событием.

    Событие о восьмой серии может прийти дважды из разных источников —
    зритель об этом знать не должен.
    """
    payload = {
        'template_code': template,
        'audience': {'kind': 'users', 'user_ids': [viewer.user_id]},
        'content_id': f'series-{uuid.uuid4().hex[:8]}',
        'content_version': 8,
        'context': {'film_title': 'Сериал'},
    }

    send_event(api_url, service_headers, **payload)
    mailbox.wait_for_letter(viewer.email)
    send_event(api_url, service_headers, **payload)

    mailbox.expect_no_letter('nobody@example.com', within=6)
    assert len(mailbox.letters_for(viewer.email)) == 1


def test_new_content_version_is_sent(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """О девятой серии письмо приходит, хотя о восьмой уже писали."""
    content_id = f'series-{uuid.uuid4().hex[:8]}'
    base = {
        'template_code': template,
        'audience': {'kind': 'users', 'user_ids': [viewer.user_id]},
        'content_id': content_id,
        'context': {'film_title': 'Сериал'},
    }

    send_event(api_url, service_headers, content_version=8, **base)
    mailbox.wait_for_letter(viewer.email)
    send_event(api_url, service_headers, content_version=9, **base)

    _wait_for_count(viewer.email, expected=2)


def test_event_to_many_viewers_is_split_into_batches(
    api_url: str, service_headers: dict[str, str], template: str,
) -> None:
    """Событие на несколько зрителей доходит до каждого.

    Получателей больше размера пачки, поэтому проверяется и то, что пачки
    режутся правильно и ни одна не теряется.
    """
    viewers = [register(first_name=f'Зритель{number}') for number in range(3)]

    send_event(
        api_url, service_headers,
        template_code=template,
        audience={'kind': 'users', 'user_ids': [viewer.user_id for viewer in viewers]},
        context={'film_title': 'Матрица'},
    )

    for viewer in viewers:
        letter = mailbox.wait_for_letter(viewer.email)
        assert viewer.email in letter.to


def test_event_to_all_viewers_reaches_them(
    api_url: str, service_headers: dict[str, str], template: str,
) -> None:
    """Рассылка «всем» доходит до зрителей с подтверждённой почтой.

    Это второй пункт задания на модуль: одинаковое письмо всем пользователям.
    """
    viewer = register(first_name='Всем')

    send_event(
        api_url, service_headers,
        template_code=template,
        audience={'kind': 'all'},
        context={'film_title': 'Подборка недели'},
    )

    letter = mailbox.wait_for_letter(viewer.email)
    assert 'Подборка недели' in letter.html


def test_viewer_without_email_is_skipped(
    api_url: str, service_headers: dict[str, str], template: str,
) -> None:
    """Зритель без адреса не ломает рассылку, а просто в неё не попадает."""
    login = f'noemail-{uuid.uuid4().hex[:10]}'
    signup = requests.post(
        f'{settings.auth_url}/auth/api/v1/signup',
        json={'login': login, 'password': PASSWORD},
        timeout=10,
    )
    signup.raise_for_status()

    response = send_event(
        api_url, service_headers,
        template_code=template,
        audience={'kind': 'users', 'user_ids': [signup.json()['id']]},
        context={'film_title': 'Матрица'},
    )

    assert response.status_code == HTTPStatus.ACCEPTED
    mailbox.expect_no_letter(f'{login}@example.com', within=5)


def test_unsubscribe_link_from_the_letter_works(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """Ссылка отписки **из письма** действительно отписывает.

    Проверять эндпоинт отдельно недостаточно: письмо может нести ссылку без
    идентификатора и подписи, и тогда отписаться из письма нельзя, хотя сам
    эндпоинт работает. Именно так и было до этой проверки.
    """
    send_event(
        api_url, service_headers,
        template_code=template,
        audience={'kind': 'users', 'user_ids': [viewer.user_id]},
        context={'film_title': 'Матрица'},
    )
    letter = mailbox.wait_for_letter(viewer.email)

    link = _unsubscribe_link_of(letter.html)
    assert link is not None, 'в письме нет ссылки отписки'
    response = requests.get(link, timeout=10)

    assert response.status_code == HTTPStatus.NO_CONTENT
    # Отписка видна зрителю: иначе он не поймёт, почему письма пропали, и не
    # сможет вернуться.
    listed = requests.get(f'{api_url}/me/subscriptions', headers=viewer.auth_headers, timeout=10)
    assert listed.json()['unsubscribed_all'] is True
    # И, главное, следующее письмо не приходит.
    mailbox.clear()
    send_event(
        api_url, service_headers,
        template_code=template,
        audience={'kind': 'users', 'user_ids': [viewer.user_id]},
        context={'film_title': 'Матрица'},
    )
    mailbox.expect_no_letter(viewer.email, within=8)


def _unsubscribe_link_of(html: str) -> str | None:
    """Достаёт ссылку отписки из письма так же, как её нажал бы человек."""
    import html as html_module
    import re

    found = re.search(r'href="([^"]*unsubscribe[^"]*)"', html)
    return html_module.unescape(found.group(1)) if found else None


def test_letter_has_plain_and_html_parts(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """У письма есть и текстовая, и HTML-часть.

    С одним лишь `set_content` зритель получил бы набор тегов — этой граблей
    урок пугает прямо.
    """
    send_event(
        api_url, service_headers,
        template_code=template,
        audience={'kind': 'users', 'user_ids': [viewer.user_id]},
        context={'film_title': 'Матрица'},
    )

    letter = mailbox.wait_for_letter(viewer.email)
    assert '<p>' in letter.html
    assert '<p>' not in letter.text
    assert 'Матрица' in letter.text


def _wait_for_count(address: str, expected: int, timeout: float = 30.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(mailbox.letters_for(address)) >= expected:
            return
        time.sleep(0.5)
    raise AssertionError(f'На {address} пришло {len(mailbox.letters_for(address))} писем вместо {expected}')
