"""Как данные показываются человеку: склонения, время, рейтинг."""

import pytest

from presentation import error_text, input_value, plural, rating, seats, when


@pytest.mark.parametrize(
    'number, expected',
    [(1, '1 место'), (2, '2 места'), (4, '4 места'), (5, '5 мест'), (11, '11 мест'), (21, '21 место'),
     (22, '22 места'), (112, '112 мест')],
)
def test_seats_declension(number, expected):
    """1 место, 2 места, 5 мест, 11 мест, 21 место — по правилам русского языка."""
    assert seats(number) == expected


def test_plural_generic():
    """Склонение работает для любых слов."""
    assert plural(3, 'оценка', 'оценки', 'оценок') == '3 оценки'


def test_when_is_moscow_time():
    """Время показа — по Москве, с днём недели и месяцем по-русски."""
    assert when('2026-10-17T16:00:00Z') == 'сб, 17 окт, 19:00'
    assert input_value('2026-10-17T16:00:00+00:00') == '2026-10-17T19:00'


def test_rating_text():
    """Рейтинг с запятой и числом оценок; без оценок — «нет оценок»."""
    assert rating({'average': 4.666, 'votes': 3}) == '★ 4,7 · 3 оценки'
    assert rating({'average': None, 'votes': 0}) == 'нет оценок'


def test_unknown_error_code_has_generic_text():
    """Неизвестный код ошибки не показывается зрителю как есть."""
    assert error_text('some_internal_code') == 'Не получилось. Попробуйте ещё раз.'
    assert error_text(None) is None
