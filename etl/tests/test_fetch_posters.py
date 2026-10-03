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
