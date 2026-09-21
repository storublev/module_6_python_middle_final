"""Преобразование события из брокера в строку аналитического хранилища."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from models.event import COLUMN_NAMES, COLUMNS, NO_UUID, EventFormatError, to_row, to_rows


def column(row, name: str):
    return row[COLUMN_NAMES.index(name)]


def test_row_follows_the_column_order(make_event) -> None:
    """Длина строки совпадает с числом колонок: строка строится по тому же списку."""
    row = to_row(make_event('click'))

    assert len(row) == len(COLUMNS)


def test_storage_schema_matches_the_columns() -> None:
    """Имена и порядок колонок совпадают со схемой в schema/001_events.sql.

    Схема живёт в SQL-файле, а строки собираются в Python: единственное, что
    удерживает их вместе, — этот тест. Без него добавленное в SQL поле молча
    сдвинуло бы значения соседних колонок.
    """
    sql = (Path(__file__).parents[2] / 'schema' / '001_events.sql').read_text(encoding='utf-8')
    body = sql.split('CREATE TABLE IF NOT EXISTS ugc.events', 1)[1]
    body = body[body.index('(') + 1:body.index(')\n')]
    names = [
        line.strip().split()[0]
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith('--')
    ]

    assert names == list(COLUMN_NAMES)


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


@pytest.mark.parametrize('value', [-5, 2 ** 32, 2 ** 40])
def test_number_outside_uint32_is_rejected(make_event, value) -> None:
    """Число вне диапазона колонки отбраковывается, а не обрезается.

    Обрезанное молча исказило бы аналитику, необрезанное — уронило бы вставку
    всей пачки и остановило перенос.
    """
    with pytest.raises(EventFormatError):
        to_row(make_event('page_view', duration_ms=value))


def test_largest_uint32_value_is_accepted(make_event) -> None:
    """Граница диапазона — ещё годное значение."""
    row = to_row(make_event('search_filters_applied', results_count=2 ** 32 - 1))

    assert column(row, 'results_count') == 2 ** 32 - 1


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


@pytest.mark.parametrize('field', ['client', 'filters'])
@pytest.mark.parametrize('value', [['web'], 'web', 42])
def test_nested_field_of_wrong_type_is_rejected(make_event, field, value) -> None:
    """Вложенное поле не того типа отбраковывается, а не роняет разбор.

    До проверки `.get()` у списка давал AttributeError — он не ловился как
    ошибка формата, ETL падал до подтверждения смещений и после перезапуска
    падал на той же записи снова.
    """
    with pytest.raises(EventFormatError):
        to_row(make_event('search_filters_applied', **{field: value}))


def test_broken_nested_field_does_not_take_the_batch_down(make_event) -> None:
    """В потоке событие с испорченным вложенным полем пропускается, соседние едут дальше."""
    events = [
        make_event('click'),
        make_event('click', client=['web']),
        make_event('search_filters_applied', filters=['genre']),
        make_event('page_view'),
    ]

    rows = list(to_rows(events))

    assert len(rows) == 2
