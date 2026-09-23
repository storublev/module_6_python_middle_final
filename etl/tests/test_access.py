"""Метка доступа фильма: по подписке — фильмы, вышедшие менее трёх лет назад.

Запуск из каталога etl: python -m pytest tests
"""

from datetime import date

import pytest

from access import PUBLIC, SUBSCRIPTION, access_level, subscription_threshold
from core.schemas import MOVIES_MAPPING
from models.dataclasses import Movie

TODAY = date(2026, 9, 15)


@pytest.mark.parametrize("creation_date, expected", [
    (date(2026, 9, 1), SUBSCRIPTION),
    (date(2023, 9, 16), SUBSCRIPTION),
    (date(2023, 9, 15), PUBLIC),
    (date(1977, 5, 25), PUBLIC),
    (date(2027, 1, 1), SUBSCRIPTION),
    (None, PUBLIC),
], ids=["this year", "day before 3 years", "exactly 3 years", "old", "announced", "no date"])
def test_access_level(creation_date, expected):
    """Новинки младше трёх лет и анонсы — по подписке; старые фильмы и фильмы без даты — всем."""
    assert access_level(creation_date, today=TODAY) == expected


def test_threshold_on_leap_day():
    """29 февраля минус три года — 28 февраля, а не ошибка."""
    assert subscription_threshold(date(2028, 2, 29), years=3) == date(2025, 2, 28)


def test_movie_document_has_access_fields():
    """Документ фильма содержит дату выхода и метку доступа, и оба поля есть в маппинге индекса."""
    document = Movie(id="1", title="New", creation_date=date.today()).to_es_document()

    assert document["creation_date"] == date.today().isoformat()
    assert document["access_level"] == SUBSCRIPTION
    assert set(document) <= set(MOVIES_MAPPING["properties"])


def test_movie_without_date_is_public():
    """Фильм без даты выхода индексируется с пустой датой и доступен всем."""
    document = Movie(id="1", title="Old").to_es_document()

    assert (document["creation_date"], document["access_level"]) == (None, PUBLIC)
