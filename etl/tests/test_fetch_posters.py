"""Подбор обложек: какое совпадение из поиска IMDb считается тем же фильмом.

Запуск из каталога etl: python -m pytest tests
"""

from scripts.fetch_posters import ambiguous_titles, best_match, normalize, resized

IMAGE = {"imageUrl": "https://m.media-amazon.com/images/M/abc@._V1_.jpg"}


def test_normalize_ignores_case_punctuation_and_diacritics():
    """Регистр, пунктуация, диакритика и «&» не мешают совпадению названий."""
    assert normalize("Star Wars: Episode IV – A New Hope") == "star wars episode iv a new hope"
    assert normalize("Amélie & Co.") == "amelie and co"


def test_only_exact_title_matches():
    """Похожее, но другое название не подходит: чужая обложка хуже заглушки."""
    candidates = [{"id": "tt1", "l": "Star Wars: The Clone Wars", "qid": "movie", "i": IMAGE}]

    assert best_match("Star Wars", "movie", candidates) is None


def test_same_kind_wins_over_popularity():
    """Среди точных совпадений выбирается запись того же типа, даже если она ниже в выдаче."""
    candidates = [
        {"id": "tt1", "l": "Star Trek", "qid": "tvSeries", "i": IMAGE},
        {"id": "tt2", "l": "Star Trek", "qid": "movie", "i": IMAGE},
    ]

    assert best_match("Star Trek", "movie", candidates)["id"] == "tt2"
    assert best_match("Star Trek", "tv_show", candidates)["id"] == "tt1"


def test_record_with_image_preferred():
    """При равном типе выигрывает запись с обложкой; записи, не являющиеся фильмами, отбрасываются."""
    candidates = [
        {"id": "nm1", "l": "Star", "i": IMAGE},
        {"id": "tt1", "l": "Star", "qid": "movie"},
        {"id": "tt2", "l": "Star", "qid": "movie", "i": IMAGE},
    ]

    assert best_match("Star", "movie", candidates)["id"] == "tt2"


def test_resized_asks_cdn_for_card_size():
    """Ссылка просит у CDN картинку шириной 400 точек, а не оригинал на несколько мегабайт."""
    assert resized(IMAGE["imageUrl"]) == "https://m.media-amazon.com/images/M/abc@._V1_QL75_UX400_.jpg"
    assert resized("https://example.com/poster.jpg") == "https://example.com/poster.jpg"


def test_ambiguous_titles_are_skipped():
    """Несколько фильмов каталога под одним названием не различить без года — обложку им не подбираем."""
    films = [{"title": "My Lucky Star"}, {"title": "my lucky star!"}, {"title": "Star Wars"}]

    assert ambiguous_titles(films) == {"my lucky star"}


def test_kinopoisk_match_needs_exact_title_and_kind():
    """Кинопоиск: совпадение по английскому или оригинальному названию и типу; два одинаковых — пропуск."""
    from scripts.fetch_kinopoisk import best_match as kp_match, record

    film = {"filmId": 333, "nameEn": "Star Wars", "type": "FILM", "nameRu": "Звёздные войны",
            "year": "1977", "rating": "8.1", "posterUrlPreview": "https://k/kp_small/333.jpg",
            "genres": [{"genre": "фантастика"}], "countries": [{"country": "США"}]}
    series = {**film, "filmId": 1, "type": "TV_SERIES"}

    assert kp_match("Star Wars", "movie", [series, film])["filmId"] == 333
    assert kp_match("Star Wars", "movie", [film, {**film, "filmId": 2}]) is None
    assert kp_match("Star Trek", "movie", [film]) is None
    row = record("f1", film)
    assert (row["found"], row["year"], row["rating"], row["genres"]) == (True, 1977, 8.1, ["фантастика"])
    assert record("f2", None)["found"] is False


def test_kinopoisk_placeholder_is_not_a_poster():
    """Заглушка Кинопоиска «нет постера» обложкой не считается."""
    from scripts.fetch_kinopoisk import poster

    assert poster({"posterUrlPreview": "https://k/images/posters/kp/no-poster.png"}) is None
