"""Имена и границы месячных секций истории входов."""

from datetime import date

import pytest

from storage.partitions import (
    month_start,
    months_from,
    next_month,
    partition_bounds,
    partition_name,
)


@pytest.mark.parametrize(
    'day, expected',
    [
        pytest.param(date(2026, 9, 16), date(2026, 9, 1), id='середина месяца'),
        pytest.param(date(2026, 9, 1), date(2026, 9, 1), id='первое число'),
        pytest.param(date(2026, 9, 30), date(2026, 9, 1), id='последний день'),
    ],
)
def test_month_start(day, expected):
    """Секция определяется месяцем даты, а не самой датой."""
    assert month_start(day) == expected


@pytest.mark.parametrize(
    'month, expected',
    [
        pytest.param(date(2026, 9, 1), date(2026, 10, 1), id='обычный месяц'),
        pytest.param(date(2026, 12, 1), date(2027, 1, 1), id='переход через год'),
    ],
)
def test_next_month(month, expected):
    """Следующий месяц считается и через границу года."""
    assert next_month(month) == expected


def test_partition_name_is_sortable():
    """Имя секции содержит год и месяц с ведущими нулями: секции сортируются по имени."""
    assert partition_name(date(2026, 9, 16)) == 'login_history_y2026m09'


def test_partitions_of_neighbouring_months_differ():
    """У соседних месяцев разные секции."""
    assert partition_name(date(2026, 9, 1)) != partition_name(date(2026, 10, 1))


def test_bounds_cover_the_whole_month():
    """Границы секции — от первого числа месяца до первого числа следующего."""
    assert partition_bounds(date(2026, 9, 16)) == (date(2026, 9, 1), date(2026, 10, 1))


def test_bounds_of_neighbouring_months_touch_without_gap():
    """Секции соседних месяцев смыкаются: ни одна дата не остаётся без секции."""
    _, september_end = partition_bounds(date(2026, 9, 1))
    october_start, _ = partition_bounds(date(2026, 10, 1))

    assert september_end == october_start


def test_months_from_starts_with_the_current_month():
    """Подготовка секций начинается с текущего месяца, а не со следующего."""
    assert next(iter(months_from(date(2026, 9, 16), 3))) == date(2026, 9, 1)


def test_months_from_returns_consecutive_months():
    """Месяцы идут подряд и переходят через год."""
    assert list(months_from(date(2026, 11, 20), 3)) == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)]


def test_months_from_counts_requested_months():
    """Запрошено столько месяцев, сколько и вернулось."""
    assert len(list(months_from(date(2026, 9, 1), 12))) == 12
