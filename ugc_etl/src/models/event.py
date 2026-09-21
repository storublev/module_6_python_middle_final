"""Преобразование события из брокера в строку аналитического хранилища.

Событие приходит разреженным: у клика нет доли просмотра, у смены качества нет
страницы. В колоночном хранилище колонки одни и те же для всех типов, поэтому
недостающие заполняются нулями и пустыми строками — так дешевле и по месту
(пустая строка сжимается почти в ничто), и по запросам (нет `Nullable`,
который хранит отдельную колонку с признаком).

Имя колонки, её тип и способ достать значение объявлены **в одном месте** —
в `COLUMNS`. Раньше имена, типы и значения лежали тремя списками, связанными
только порядком: добавив поле в середину, легко было сдвинуть значения, а у
колонок одинакового типа такая ошибка не вылезла бы ни в одной проверке —
страница просто оказалась бы в колонке источника перехода.
"""

import logging
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Пустой UUID вместо NULL: «фильма нет» и «фильм нулевой» для аналитики одно
# и то же, а Nullable(UUID) стоил бы лишней колонки с признаком.
NO_UUID = UUID(int=0)

# Верхняя граница числовых колонок: в хранилище они UInt32. Значение больше
# сюда не влезет, и, дойдя до вставки, остановило бы перенос всей пачки —
# поэтому такое событие отбраковывается здесь, как нарушение контракта.
UINT32_MAX = 2 ** 32 - 1


class EventFormatError(ValueError):
    """Событие не приводится к строке: нет обязательного поля или оно не того типа."""


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
    """Целое в диапазоне UInt32.

    Значение вне диапазона не обрезается, а отбраковывается: обрезанное число
    молча исказило бы аналитику, а необрезанное уронило бы вставку всей пачки.
    """
    number = int(value or 0)
    if not 0 <= number <= UINT32_MAX:
        raise EventFormatError(f'число {number} вне диапазона UInt32')
    return number


def _mapping(value: Any, name: str) -> dict:
    """Вложенный объект события: пустой, если его нет, и ошибка, если это не объект."""
    if value in (None, '', {}):
        return {}
    if not isinstance(value, dict):
        raise EventFormatError(f'поле {name}: ожидался объект, получено {type(value).__name__}')
    return value


@dataclass(frozen=True)
class Column:
    """Колонка хранилища: имя, тип и то, как получить её значение из события.

    `value` принимает само событие и уже проверенный объект `client`: доставать
    его в каждой колонке заново значило бы проверять его тип двадцать раз.
    """

    name: str
    type: str
    value: Callable[[dict, dict], Any]


# Порядок колонок здесь и в schema/001_events.sql должен совпадать — это
# проверяет unit-тест, читающий сам файл схемы.
COLUMNS: tuple[Column, ...] = (
    Column('event_id', 'UUID', lambda event, client: _uuid(event.get('event_id'), required=True)),
    Column('event_type', 'LowCardinality(String)', lambda event, client: _text(event.get('event_type'), True)),
    # Время на стороне клиента и время приёма сервисом: по их расхождению
    # видно, насколько врут часы клиента и долго ли событие ждало отправки.
    Column('occurred_at', "DateTime64(3, 'UTC')", lambda event, client: _moment(event.get('occurred_at'), True)),
    Column('received_at', "DateTime64(3, 'UTC')", lambda event, client: _moment(event.get('received_at'), True)),
    Column('user_id', 'UUID', lambda event, client: _uuid(event.get('user_id'), required=True)),
    Column('session_id', 'UUID', lambda event, client: _uuid(event.get('session_id'), required=True)),

    Column('film_id', 'UUID', lambda event, client: _uuid(event.get('film_id'))),
    Column('page', 'String', lambda event, client: _text(event.get('page'))),
    Column('referrer', 'String', lambda event, client: _text(event.get('referrer'))),
    Column('element_type', 'LowCardinality(String)', lambda event, client: _text(event.get('element_type'))),
    Column('element_id', 'String', lambda event, client: _text(event.get('element_id'))),

    Column('duration_ms', 'UInt32', lambda event, client: _number(event.get('duration_ms'))),
    Column('position_ms', 'UInt32', lambda event, client: _number(event.get('position_ms'))),
    Column('watched_ratio', 'Float32', lambda event, client: float(event.get('watched_ratio') or 0)),
    Column('quality_from', 'LowCardinality(String)', lambda event, client: _text(event.get('quality_from'))),
    Column('quality_to', 'LowCardinality(String)', lambda event, client: _text(event.get('quality_to'))),
    Column('search_query', 'String', lambda event, client: _text(event.get('query'))),
    Column(
        'filters',
        'Map(LowCardinality(String), String)',
        lambda event, client: {
            str(name): str(value) for name, value in _mapping(event.get('filters'), 'filters').items()
        },
    ),
    Column('results_count', 'UInt32', lambda event, client: _number(event.get('results_count'))),

    # Клиент: по этим колонкам аналитика режет данные по платформам.
    Column('platform', 'LowCardinality(String)', lambda event, client: _text(client.get('platform'))),
    Column('device', 'String', lambda event, client: _text(client.get('device'))),
    Column('app_version', 'String', lambda event, client: _text(client.get('app_version'))),
)

# То, что ждёт драйвер: два списка в одном и том же порядке, собранные из COLUMNS.
COLUMN_NAMES: tuple[str, ...] = tuple(column.name for column in COLUMNS)
COLUMN_TYPES: tuple[str, ...] = tuple(column.type for column in COLUMNS)


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

    Значения собираются по именам колонок, а строка строится по тому же
    единственному списку COLUMNS — перепутать порядок негде.

    Raises:
        EventFormatError: у события нет обязательного поля или оно не того типа.
    """
    values = to_fields(event)
    return tuple(values[column.name] for column in COLUMNS)


def to_fields(event: dict) -> dict[str, Any]:
    """Достаёт из события значения всех колонок — словарём, по именам.

    Raises:
        EventFormatError: у события нет обязательного поля или оно не того типа.
    """
    if not isinstance(event, dict):
        raise EventFormatError(f'ожидался объект, получено {type(event).__name__}')
    # Вложенные поля проверяются по типу до обращения к ним: сообщение с
    # client: ["web"] иначе уронило бы разбор на .get() у списка, а это
    # AttributeError — он не ловится как ошибка формата и вынес бы весь
    # перенос. После перезапуска ETL прочитал бы то же сообщение и упал снова.
    client = _mapping(event.get('client'), 'client')
    try:
        return {column.name: column.value(event, client) for column in COLUMNS}
    except EventFormatError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise EventFormatError(str(error)) from error
