"""Чтение почтового ящика через HTTP-API Mailpit.

Тест проверяет письмо, а не журнал отправителя: тема, тело и адрес читаются из
того, что на самом деле дошло до почтового сервера. Иначе проверялось бы
намерение сервиса, а не результат.
"""

import time
from dataclasses import dataclass

import requests

from tests.functional.settings import settings


@dataclass(frozen=True)
class Letter:
    """Письмо, как его отдал почтовый сервер."""

    subject: str
    to: list[str]
    text: str
    html: str

    def addressed_to(self, address: str) -> bool:
        return address in self.to


def clear() -> None:
    """Вычищает ящик: каждый тест начинает с пустого."""
    requests.delete(f'{settings.mailpit_url}/api/v1/messages', timeout=5).raise_for_status()


def letters_for(address: str) -> list[Letter]:
    """Все письма, дошедшие до адреса."""
    response = requests.get(
        f'{settings.mailpit_url}/api/v1/search', params={'query': f'to:{address}'}, timeout=5,
    )
    response.raise_for_status()
    return [_letter(item['ID']) for item in response.json().get('messages', [])]


def wait_for_letter(address: str, timeout: float | None = None) -> Letter:
    """Ждёт письма на адрес.

    Письмо проходит три очереди и трёх воркеров, поэтому мгновенным быть не
    обязано. Ждём с запасом, но не бесконечно: зависший конвейер — это тоже
    провал теста.
    """
    deadline = time.monotonic() + (timeout or settings.mail_timeout)
    while time.monotonic() < deadline:
        letters = letters_for(address)
        if letters:
            return letters[0]
        time.sleep(0.5)
    raise AssertionError(f'Письмо на {address} не пришло за {timeout or settings.mail_timeout} с')


def expect_no_letter(address: str, within: float = 8.0) -> None:
    """Убеждается, что письма нет.

    Отрицательную проверку нельзя делать мгновенно: письмо могло ещё не дойти,
    и тест прошёл бы, ничего не проверив. Поэтому ждём столько, сколько в
    норме хватает на весь конвейер.
    """
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if letters_for(address):
            raise AssertionError(f'На {address} пришло письмо, хотя не должно было')
        time.sleep(0.5)


def _letter(message_id: str) -> Letter:
    response = requests.get(f'{settings.mailpit_url}/api/v1/message/{message_id}', timeout=5)
    response.raise_for_status()
    body = response.json()
    return Letter(
        subject=body.get('Subject', ''),
        to=[item['Address'] for item in body.get('To', [])],
        text=body.get('Text', ''),
        html=body.get('HTML', ''),
    )
