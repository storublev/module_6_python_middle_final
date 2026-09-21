"""Преобразование события из брокера в строку аналитического хранилища."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from models.event import COLUMN_TYPES, COLUMNS, NO_UUID, EventFormatError, to_row, to_rows


def column(row, name: str):
    return row[COLUMNS.index(name)]


def test_columns_and_types_match() -> None:
    """Имена и типы колонок идут парами: строки передаются драйверу позиционно."""
    assert len(COLUMNS) == len(COLUMN_TYPES)


def test_click_event_becomes_a_row(make_event) -> None:
    """Клик превращается в строку со своими полями."""
    event = make_event('click', element_id='recommended-3')

    row = to_row(event)

    assert column(row, 'event_type') == 'click'
    assert column(row, 'element_type') == 'film_card'
    assert column(row, 'element_id') == 'recommended-3'
    assert column(row, 'page') == '/catalog'


def test_missing_fields_become_empty_values(make_event) -> None:
    """У события своего типа чужие поля пустые, а не NULL: так дешевле и по месту, и по запросам."""
    row = to_row(make_event('page_view'))

    assert column(row, 'film_id') == NO_UUID
    assert column(row, 'watched_ratio') == 0
    assert column(row, 'quality_from') == ''
    assert column(row, 'filters') == {}


def test_time_is_converted_to_utc(make_event) -> None:
    """Время клиента приводится к UTC: сравнивать события нужно по одной шкале."""
    row = to_row(make_event('click', occurred_at='2026-09-21T19:04:11+03:00'))

    assert column(row, 'occurred_at') == datetime(2026, 9, 21, 16, 4, 11, tzinfo=UTC)


def test_time_without_zone_is_treated_as_utc(make_event) -> None:
    """Время без пояса считается временем UTC, а не временем машины ETL."""
    row = to_row(make_event('click', occurred_at='2026-09-21T16:04:11'))

    assert column(row, 'occurred_at') == datetime(2026, 9, 21, 16, 4, 11, tzinfo=UTC)


def test_client_fields_are_unpacked(make_event) -> None:
    """Вложенный объект клиента раскладывается по колонкам."""
    row = to_row(make_event('click'))

    assert column(row, 'platform') == 'web'
    assert column(row, 'device') == 'pytest'
    assert column(row, 'app_version') == '1.0'


def test_search_filters_are_kept_as_a_map(make_event) -> None:
    """Фильтры поиска едут словарём: их набор меняется вместе с интерфейсом."""
    row = to_row(make_event('search_filters_applied'))

    assert column(row, 'filters') == {'genre': 'sci-fi'}
    assert column(row, 'search_query') == 'нолан'
    assert column(row, 'results_count') == 12


def test_watched_ratio_is_kept(make_event) -> None:
    """Доля просмотра доезжает до хранилища: по ней считаются недосмотренные фильмы."""
    row = to_row(make_event('video_completed', watched_ratio=0.34))

    assert column(row, 'watched_ratio') == pytest.approx(0.34)


def test_identifiers_become_uuid(make_event) -> None:
    """Идентификаторы приводятся к UUID: в колонке UUID строка не нужна."""
    row = to_row(make_event('click'))

    assert isinstance(column(row, 'event_id'), UUID)
    assert isinstance(column(row, 'user_id'), UUID)


def test_negative_number_is_clamped(make_event) -> None:
    """Отрицательное число не доезжает до UInt32, где оно стало бы огромным."""
    row = to_row(make_event('page_view', duration_ms=-5))

    assert column(row, 'duration_ms') == 0


@pytest.mark.parametrize('missing', ['event_id', 'user_id', 'session_id', 'occurred_at', 'event_type'])
def test_event_without_required_field_is_rejected(make_event, missing) -> None:
    """Без обязательного поля событие в строку не превращается."""
    event = make_event('click')
    del event[missing]

    with pytest.raises(EventFormatError):
        to_row(event)


def test_event_with_broken_identifier_is_rejected(make_event) -> None:
    """Идентификатор не в формате UUID — событие непригодно."""
    with pytest.raises(EventFormatError):
        to_row(make_event('click', user_id='не-uuid'))


def test_non_object_is_rejected() -> None:
    """Сообщение, оказавшееся не объектом, отклоняется, а не роняет разбор."""
    with pytest.raises(EventFormatError):
        to_row(['список'])


def test_unusable_events_are_skipped_not_raised(make_event) -> None:
    """В потоке непригодное событие пропускается: уронить перенос на нём нельзя."""
    rows = list(to_rows([make_event('click'), {'event_type': 'click'}, make_event('page_view')]))

    assert len(rows) == 2


def test_to_rows_is_lazy(make_event) -> None:
    """Преобразование идёт генератором: второй копии пачки в памяти не появляется."""
    rows = to_rows([make_event('click')])

    assert iter(rows) is rows
