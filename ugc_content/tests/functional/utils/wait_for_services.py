"""Ожидание MongoDB и сервиса перед запуском тестов.

Без ожидания тесты стартуют раньше, чем сервис успевает подняться, и падают
не по делу: в отчёте это выглядит как сломанный сервис, а на самом деле —
как гонка при старте окружения.
"""

import sys
from time import monotonic, sleep

import requests
from pymongo import MongoClient

from tests.functional.settings import settings

INTERVAL = 1.0


def wait(name: str, check) -> None:
    deadline = monotonic() + settings.wait_timeout
    while monotonic() < deadline:
        try:
            if check():
                print(f'{name}: готов', flush=True)
                return
        except Exception as error:  # noqa: BLE001 — пока сервис поднимается, ошибки ожидаемы
            last = error
        else:
            last = None
        sleep(INTERVAL)
    print(f'{name}: не дождались за {settings.wait_timeout} с ({last})', file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=1000, uuidRepresentation='standard')
    wait('MongoDB', lambda: client.admin.command('ping').get('ok') == 1.0)
    client.close()
    wait(
        'сервис',
        lambda: requests.get(f'{settings.service_url}/content/api/v1/ready', timeout=2).status_code == 200,
    )


if __name__ == '__main__':
    main()
