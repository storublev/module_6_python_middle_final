"""Общие фикстуры функциональных тестов.

Тесты ходят к сервису по HTTP и проверяют результат там, где его видит
внешний мир: письмо — в почтовом ящике, уведомление — в личном кабинете,
переход — в ответе короткой ссылки. Кода сервиса они не импортируют.

Зрителя заводит **настоящий** сервис авторизации: воркер потом идёт в него за
адресом и именем, и подменять этот стык заглушкой значило бы не проверить
самое хрупкое место.
"""

import uuid
from collections.abc import Iterator

import pytest
import requests

from tests.functional.settings import settings
from tests.functional.utils import mailbox

API = '/notify/api/v1'
AUTH_API = '/auth/api/v1'
SERVICE_TOKEN_HEADER = 'X-Service-Token'  # noqa: S105 - имя заголовка, а не пароль
PASSWORD = 'functional-Tests-1'


@pytest.fixture
def api_url() -> str:
    return f'{settings.service_url}{API}'


@pytest.fixture
def service_headers() -> dict[str, str]:
    """Заголовки служебного доступа: так ходят сервисы и админка."""
    return {SERVICE_TOKEN_HEADER: settings.service_token}


class Viewer:
    """Зритель в сервисе авторизации: его учётная запись, токен и почта."""

    def __init__(self, user_id: str, login: str, email: str, token: str) -> None:
        self.user_id = user_id
        self.login = login
        self.email = email
        self.token = token

    @property
    def auth_headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.token}'}


def register(first_name: str = 'Томас', last_name: str = 'Андерсон', timezone: str = 'Europe/Moscow') -> Viewer:
    """Заводит зрителя и заполняет его контакты.

    Контакты обязательны: без адреса воркеру некуда отправлять письмо, и
    зритель просто не попадёт в рассылку — именно так и задумано.
    """
    login = f'viewer-{uuid.uuid4().hex[:12]}'
    email = f'{login}@example.com'
    signup = requests.post(
        f'{settings.auth_url}{AUTH_API}/signup', json={'login': login, 'password': PASSWORD}, timeout=10,
    )
    signup.raise_for_status()
    user_id = signup.json()['id']

    login_response = requests.post(
        f'{settings.auth_url}{AUTH_API}/login', json={'login': login, 'password': PASSWORD}, timeout=10,
    )
    login_response.raise_for_status()
    token = login_response.json()['access_token']

    profile = requests.patch(
        f'{settings.auth_url}{AUTH_API}/users/me/profile',
        json={'email': email, 'first_name': first_name, 'last_name': last_name, 'timezone': timezone},
        headers={'Authorization': f'Bearer {token}'},
        timeout=10,
    )
    profile.raise_for_status()
    return Viewer(user_id=user_id, login=login, email=email, token=token)


@pytest.fixture
def viewer() -> Viewer:
    return register()


@pytest.fixture
def other_viewer() -> Viewer:
    return register(first_name='Тринити', last_name='Ноль')


@pytest.fixture(autouse=True)
def empty_mailbox() -> Iterator[None]:
    """Каждый тест начинает с пустого ящика и убирает за собой.

    Общий ящик между тестами превратил бы «письмо пришло» в «когда-то кому-то
    приходило письмо».
    """
    mailbox.clear()
    yield
    mailbox.clear()


@pytest.fixture
def template(api_url: str, service_headers: dict[str, str]) -> Iterator[str]:
    """Свой шаблон на время теста, с уникальным кодом."""
    code = f'test_{uuid.uuid4().hex[:10]}'
    created = requests.post(
        f'{api_url}/templates',
        json={
            'code': code,
            'name': 'Проверочное письмо',
            'channel': 'email',
            'subject': 'Здравствуйте, {{ first_name }}',
            'body': '<p>{{ full_name }}, смотрите «{{ film_title }}»</p>'
                    '<p><a href="{{ unsubscribe_url }}">Отписаться</a></p>',
            'is_active': True,
        },
        headers=service_headers,
        timeout=10,
    )
    created.raise_for_status()
    yield code
    requests.delete(f'{api_url}/templates/{code}', headers=service_headers, timeout=10)


def send_event(api_url: str, headers: dict[str, str], **overrides: object) -> requests.Response:
    """Отправляет событие с разумными значениями по умолчанию."""
    payload: dict = {
        'event_id': str(uuid.uuid4()),
        'routing_key': 'film-reporting.v1.episode-added',
        'template_code': 'welcome',
        'channel': 'email',
        'audience': {'kind': 'users', 'user_ids': []},
    }
    payload.update(overrides)
    return requests.post(f'{api_url}/events', json=payload, headers=headers, timeout=10)
