"""Метка доступа фильма: по подписке — фильмы, вышедшие менее трёх лет назад.

Запуск из каталога etl: python -m pytest tests
"""

from datetime import date

import pytest

from access import PUBLIC, SUBSCRIPTION, access_level, subscription_threshold
from core.schemas import MOVIES_MAPPING
from models.dataclasses import Movie
from pipelines import movie_document

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


def test_movie_document_carries_type_and_poster():
    """Тип фильма и обложка из каталога доезжают до документа индекса и описаны в маппинге."""
    row = {
        "id": "1", "title": "Star Wars", "type": "movie", "imdb_id": "tt0076759",
        "poster_url": "https://m.media-amazon.com/images/M/poster._V1_QL75_UX400_.jpg",
        "genres": [], "persons": [],
    }

    document = movie_document(row)

    assert (document["type"], document["imdb_id"]) == ("movie", "tt0076759")
    assert document["poster_url"].endswith("UX400_.jpg")
    assert {"type", "poster_url", "imdb_id"} <= set(MOVIES_MAPPING["properties"])


def test_movie_without_poster_is_indexed_with_empty_link():
    """Фильм без найденной обложки индексируется с пустой ссылкой: заглушку рисует интерфейс."""
    document = movie_document({"id": "1", "title": "Unknown", "type": "tv_show", "genres": [], "persons": []})

    assert (document["type"], document["poster_url"], document["imdb_id"]) == ("tv_show", None, None)


def test_movie_document_carries_kinopoisk_data():
    """Русское название и описание, рейтинг и год Кинопоиска доезжают до индекса и описаны в маппинге."""
    row = {
        "id": "1", "title": "Star Wars", "type": "movie", "poster_url": "/posters/1.jpg",
        "kinopoisk_id": 333, "title_ru": "Звёздные войны", "description_ru": "Татуин. Планета-пустыня.",
        "kinopoisk_rating": 8.1, "year": 1977, "genres": [], "persons": [],
    }

    document = movie_document(row)

    assert (document["kinopoisk_id"], document["title_ru"], document["year"]) == ("333", "Звёздные войны", 1977)
    assert document["poster_url"] == "/posters/1.jpg"
    fields = {"kinopoisk_id", "title_ru", "description_ru", "kinopoisk_rating", "year"}
    assert fields <= set(MOVIES_MAPPING["properties"])
