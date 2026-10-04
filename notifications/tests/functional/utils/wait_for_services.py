"""Ждём, пока поднимутся сервисы, и только потом запускаем тесты.

Проверка живости контейнера говорит, что процесс отвечает, но конвейер
уведомлений готов позже: воркерам нужно объявить очереди и подключиться к
брокеру. Поэтому ждём ещё и появления очередей — иначе первое же событие
уйдёт в пустоту, а тест обвинит в этом код.
"""

import sys
import time

import requests

from tests.functional.settings import settings

QUEUES = ('notify.plan', 'notify.render', 'notify.send')


def wait(name: str, check, timeout: float) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if check():
                print(f'{name}: готов', flush=True)
                return
        except Exception as error:  # noqa: BLE001 - ждём именно недоступности
            last_error = error
        time.sleep(1)
    raise SystemExit(f'{name} не поднялся за {timeout} с: {last_error}')


def service_ready() -> bool:
    return requests.get(f'{settings.service_url}/notify/api/v1/health', timeout=3).status_code == 200


def auth_ready() -> bool:
    return requests.get(f'{settings.auth_url}/auth/api/openapi.json', timeout=3).status_code == 200


def mailpit_ready() -> bool:
    return requests.get(f'{settings.mailpit_url}/api/v1/messages', timeout=3).status_code == 200


def ws_ready() -> bool:
    return requests.get(f'{settings.ws_url.replace("ws://", "http://")}/health', timeout=3).status_code == 200


def queues_declared() -> bool:
    response = requests.get(f'{settings.rabbit_ui_url}/api/queues', timeout=5)
    response.raise_for_status()
    names = {queue['name'] for queue in response.json()}
    return all(queue in names for queue in QUEUES)


def main() -> int:
    wait('сервис уведомлений', service_ready, settings.wait_timeout)
    wait('сервис авторизации', auth_ready, settings.wait_timeout)
    wait('приёмник почты', mailpit_ready, settings.wait_timeout)
    wait('websocket-шлюз', ws_ready, settings.wait_timeout)
    wait('очереди брокера', queues_declared, settings.wait_timeout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
