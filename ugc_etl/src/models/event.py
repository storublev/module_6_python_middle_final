"""Преобразование события из брокера в строку аналитического хранилища.

Событие приходит разреженным: у клика нет доли просмотра, у смены качества нет
страницы. В колоночном хранилище колонки одни и те же для всех типов, поэтому
недостающие заполняются нулями и пустыми строками — так дешевле и по месту
(пустая строка сжимается почти в ничто), и по запросам (нет `Nullable`,
который хранит отдельную колонку с признаком).

Порядок колонок здесь и в `schema/001_events.sql` должен совпадать: строки
передаются в драйвер позиционно.
"""

import logging
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Пустой UUID вместо NULL: «фильма нет» и «фильм нулевой» для аналитики одно
# и то же, а Nullable(UUID) стоил бы лишней колонки с признаком.
NO_UUID = UUID(int=0)

COLUMNS = (
    'event_id', 'event_type', 'occurred_at', 'received_at', 'user_id', 'session_id',
    'film_id', 'page', 'referrer', 'element_type', 'element_id',
    'duration_ms', 'position_ms', 'watched_ratio', 'quality_from', 'quality_to',
    'search_query', 'filters', 'results_count',
    'platform', 'device', 'app_version',
)

COLUMN_TYPES = (
    'UUID', 'LowCardinality(String)', "DateTime64(3, 'UTC')", "DateTime64(3, 'UTC')", 'UUID', 'UUID',
    'UUID', 'String', 'String', 'LowCardinality(String)', 'String',
    'UInt32', 'UInt32', 'Float32', 'LowCardinality(String)', 'LowCardinality(String)',
    'String', 'Map(LowCardinality(String), String)', 'UInt32',
    'LowCardinality(String)', 'String', 'String',
)


class EventFormatError(ValueError):
    """Событие не приводится к строке: нет обязательного поля или оно не того типа."""


def to_rows(events: Iterable[dict]) -> Iterator[Sequence[Any]]:
    """Превращает поток событий в поток строк, пропуская непригодные.

    Генератор, а не список: пачка в десять тысяч событий не должна
    удваиваться в памяти ради преобразования (НФТ-12).

    Событие, которое не приводится к строке, пропускается с записью в журнал.
    Уронить на нём весь перенос нельзя: ETL перезапустится, прочитает ту же
    пачку и упадёт снова.
    """
    for event in events:
        try:
            yield to_row(event)
        except EventFormatError as error:
            logger.warning('Событие пропущено: %s', error)


def to_row(event: dict) -> Sequence[Any]:
    """Превращает одно событие в строку.

    Raises:
        EventFormatError: события нет обязательного поля или оно не того типа.
    """
    if not isinstance(event, dict):
        raise EventFormatError(f'ожидался объект, получено {type(event).__name__}')
    client = event.get('client') or {}
    try:
        return (
            _uuid(event.get('event_id'), required=True),
            _text(event.get('event_type'), required=True),
            _moment(event.get('occurred_at'), required=True),
            _moment(event.get('received_at'), required=True),
            _uuid(event.get('user_id'), required=True),
            _uuid(event.get('session_id'), required=True),
            _uuid(event.get('film_id')),
            _text(event.get('page')),
            _text(event.get('referrer')),
            _text(event.get('element_type')),
            _text(event.get('element_id')),
            _number(event.get('duration_ms')),
            _number(event.get('position_ms')),
            float(event.get('watched_ratio') or 0),
            _text(event.get('quality_from')),
            _text(event.get('quality_to')),
            _text(event.get('query')),
            {str(name): str(value) for name, value in (event.get('filters') or {}).items()},
            _number(event.get('results_count')),
            _text(client.get('platform')),
            _text(client.get('device')),
            _text(client.get('app_version')),
        )
    except (TypeError, ValueError) as error:
        raise EventFormatError(str(error)) from error


def _uuid(value: Any, required: bool = False) -> UUID:
    if value in (None, ''):
        if required:
            raise EventFormatError('обязательное поле-идентификатор отсутствует')
        return NO_UUID
    return value if isinstance(value, UUID) else UUID(str(value))


def _moment(value: Any, required: bool = False) -> datetime:
    """Время события в UTC.

    Клиенты присылают время со своим часовым поясом, а в хранилище колонка
    одна и в UTC: сравнивать вечер в Москве и вечер в Калининграде нужно по
    одной шкале.
    """
    if value in (None, ''):
        if required:
            raise EventFormatError('обязательное поле со временем отсутствует')
        return datetime.fromtimestamp(0, UTC)
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def _text(value: Any, required: bool = False) -> str:
    if value in (None, ''):
        if required:
            raise EventFormatError('обязательное текстовое поле отсутствует')
        return ''
    return str(value)


def _number(value: Any) -> int:
    """Неотрицательное целое: отрицательное значение UInt32 не примет."""
    return max(0, int(value or 0))
