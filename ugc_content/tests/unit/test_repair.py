"""Конвейеры восстановления счётчиков: форма запросов к MongoDB.

Сам пересчёт проверяется на живой базе (см. README сервиса), а здесь —
структура конвейеров. Это дёшево и ловит настоящую ошибку: без `$unset: _id`
перед `$merge` MongoDB отказывается обновлять найденный документ, потому что
идентификатор менять нельзя, и весь пересчёт откатывается.
"""

from storage.repair import FILM_RATINGS_PIPELINE, REVIEW_VOTES_PIPELINE

PIPELINES = (FILM_RATINGS_PIPELINE, REVIEW_VOTES_PIPELINE)


def stage_names(pipeline: list[dict]) -> list[str]:
    return [next(iter(stage)) for stage in pipeline]


def test_pipelines_drop_id_before_merge():
    """`_id` убирается до слияния: иначе MongoDB откажется обновлять документ."""
    for pipeline in PIPELINES:
        names = stage_names(pipeline)

        assert '$unset' in names, names
        assert names.index('$unset') < names.index('$merge'), names


def test_pipelines_merge_by_business_key():
    """Слияние идёт по ключу предметной области, а не по `_id` документа."""
    for pipeline, key, target in (
        (FILM_RATINGS_PIPELINE, 'film_id', 'film_ratings'),
        (REVIEW_VOTES_PIPELINE, 'review_id', 'reviews'),
    ):
        merge = pipeline[-1]['$merge']

        assert merge['on'] == key
        assert merge['into'] == target


def test_film_counter_is_rebuilt_from_all_fields():
    """Пересчёт восстанавливает все поля счётчика, а не часть из них."""
    grouped = FILM_RATINGS_PIPELINE[0]['$group']

    assert set(grouped) == {'_id', 'likes', 'dislikes', 'sum_rating', 'votes'}


def test_review_counters_are_merged_not_replaced():
    """У рецензии заменяются только счётчики: текст и автор остаются на месте."""
    assert REVIEW_VOTES_PIPELINE[-1]['$merge']['whenMatched'] == 'merge'
