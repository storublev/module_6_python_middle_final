"""Websocket-шлюз: авторизация подключения и мгновенная доставка."""

import asyncio
import json
import uuid
from http import HTTPStatus

import pytest
import requests
import websockets

# Подмодуль импортируется явно: у пакета websockets ленивый __getattr__, и
# `websockets.exceptions` без импорта поднимает AttributeError.
import websockets.exceptions

from tests.functional.conftest import Viewer, send_event
from tests.functional.settings import settings

CONNECT_TIMEOUT = 10
# Код закрытия «policy violation» из RFC 6455: им шлюз отвечает на чужой токен.
POLICY_VIOLATION = 1008
MESSAGE_TIMEOUT = 20


def test_connection_without_token_is_refused() -> None:
    """Без токена к потоку уведомлений не подключиться.

    Чек-лист задания говорит об этом прямо: «не забывайте про авторизацию,
    чтобы предотвратить возможность подключения сторонних пользователей».
    """
    with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
        asyncio.run(_connect_and_wait(token=None, timeout=CONNECT_TIMEOUT))

    # Код 1008 — «policy violation»: клиент должен понять, что дело в нём, и
    # не переподключаться в цикле.
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == POLICY_VIOLATION
    assert closed.value.rcvd.reason == 'not_authenticated'


def test_connection_with_forged_token_is_refused() -> None:
    """Токен, подписанный чужим ключом, не пускает."""
    import jwt

    forged = jwt.encode(
        {
            'sub': str(uuid.uuid4()), 'sid': str(uuid.uuid4()), 'jti': str(uuid.uuid4()),
            'type': 'access', 'iat': 0, 'exp': 4102444800,
        },
        'not-the-secret-key-of-this-service',
        algorithm='HS256',
    )

    with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
        asyncio.run(_connect_and_wait(token=forged, timeout=CONNECT_TIMEOUT))

    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == POLICY_VIOLATION


def test_instant_notification_reaches_open_tab(
    api_url: str, service_headers: dict[str, str], viewer: Viewer, template: str,
) -> None:
    """Уведомление приходит в открытую вкладку зрителя.

    Это второй канал доставки: в отличие от письма, он мгновенный и не требует
    почтового сервера.
    """
    received = asyncio.run(
        _receive_while(
            viewer.token,
            trigger=lambda: send_event(
                api_url, service_headers,
                template_code=template,
                channel='websocket',
                audience={'kind': 'users', 'user_ids': [viewer.user_id]},
                context={'film_title': 'Матрица'},
            ),
        ),
    )

    assert 'Матрица' in received['body']
    assert received['subject'] == 'Здравствуйте, Томас'


def test_push_requires_service_token() -> None:
    """Служебную доставку нельзя вызвать без секрета: иначе кто угодно напишет зрителю."""
    response = requests.post(
        f'{settings.ws_url.replace("ws://", "http://")}/internal/push',
        json={'user_id': str(uuid.uuid4()), 'subject': 'Подделка', 'body': 'текст'},
        timeout=10,
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_health_is_open() -> None:
    """Проверка живости шлюза отвечает без токена: её дёргает оркестратор."""
    response = requests.get(f'{settings.ws_url.replace("ws://", "http://")}/health', timeout=5)

    assert response.status_code == HTTPStatus.OK


async def _connect_and_wait(token: str | None, timeout: float) -> None:
    url = f'{settings.ws_url}/ws/notifications'
    if token:
        url = f'{url}?token={token}'
    async with websockets.connect(url, open_timeout=timeout) as connection:
        await asyncio.wait_for(connection.recv(), timeout=timeout)


async def _receive_while(token: str, trigger) -> dict:  # noqa: ANN001 - вызываемое из теста
    """Подключается, запускает событие и ждёт сообщения.

    Порядок важен: событие отправляется **после** подключения. Иначе воркер
    попробует доставить уведомление в момент, когда зрителя ещё нет в сети, и
    честно ответит «некому».
    """
    url = f'{settings.ws_url}/ws/notifications?token={token}'
    async with websockets.connect(url, open_timeout=CONNECT_TIMEOUT) as connection:
        # Небольшая пауза: соединение уже принято, но шлюз мог ещё не записать
        # его в реестр подключений.
        await asyncio.sleep(0.5)
        trigger()
        raw = await asyncio.wait_for(connection.recv(), timeout=MESSAGE_TIMEOUT)
    return json.loads(raw)
