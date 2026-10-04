"""Шаблоны писем о бронях: проходят ту же проверку, что шаблон менеджера, и собираются из данных события."""

import importlib.util
from pathlib import Path

import pytest

from services.renderer import Renderer
from tests.unit.fakes import recipient, template

MIGRATION = Path(__file__).parents[2] / 'migrations' / 'versions' / '0006_add_booking_templates.py'


def booking_templates() -> tuple:
    spec = importlib.util.spec_from_file_location('booking_templates', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TEMPLATES


@pytest.mark.parametrize('code, name, subject, body', booking_templates(), ids=lambda value: str(value)[:24])
def test_template_is_valid(renderer: Renderer, code, name, subject, body) -> None:
    """Каждый шаблон пользуется только разрешёнными переменными и собирается на пробных данных."""
    renderer.validate(subject, body)


def test_guest_letter_from_booking_event(renderer: Renderer) -> None:
    """Письмо гостю собирается из контекста события сервиса бронирования и данных получателя."""
    _, _, subject, body = next(t for t in booking_templates() if t[0] == 'booking_confirmed')
    context = {
        'film_title': 'Star Wars', 'host_name': 'Нео', 'starts_at': '17.10.2026 19:00 (MSK)', 'place': 'Зал 3',
        'address': 'Новый Арбат, 24', 'seats': 2, 'seats_left': 4, 'change': 'created',
        'action_url': 'https://practix.local/screenings/1',
    }
    letter = template('booking_confirmed', subject=subject, body=body)

    rendered_subject, html = renderer.render(letter, recipient(first_name='Тринити'), context)

    assert rendered_subject == 'Бронь на «Star Wars» — 17.10.2026 19:00 (MSK)'
    assert 'Тринити, за вами 2 мест' in html
    assert 'Зал 3, Новый Арбат, 24' in html
