"""Ждём, пока поднимутся сервисы, и только потом запускаем тесты."""

import sys
import time

import requests

from tests.functional.settings import settings


def wait(name: str, url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | str | None = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                print(f'{name}: готов', flush=True)
                return
            last_error = f'HTTP {response.status_code}'
        except requests.RequestException as error:
            last_error = error
        time.sleep(1)
    raise SystemExit(f'{name} не поднялся за {timeout} с: {last_error}')


def main() -> int:
    wait('сервис бронирования', f'{settings.service_url}/booking/api/v1/health', settings.wait_timeout)
    wait('сервис авторизации', f'{settings.auth_url}/auth/api/openapi.json', settings.wait_timeout)
    wait('заглушка соседей', f'{settings.stub_url}/_events', settings.wait_timeout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
