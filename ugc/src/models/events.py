"""Контракт событий, которые присылает клиентское приложение.

Событие — факт действия зрителя: клик, просмотр страницы или что-то из
происходящего в плеере. Типы различаются полем `event_type`, и pydantic
разбирает их размеченным объединением (discriminated union): по значению
`event_type` он сразу знает, какую модель применять, и сообщает об ошибке в
терминах этой модели, а не перечисляет все варианты подряд.

Поля лежат плоско, без вложенного `payload`: каждое событие едет в колоночное
хранилище, где вложенность пришлось бы разворачивать. Общее для всех типов
собрано в `BaseEvent`, остальное объявляет каждый тип сам.

Чего в событии нет: `user_id`. Его нельзя брать из тела запроса — клиент
прислал бы чужой. Сервис подставляет его из access-токена (см. api/security.py).
"""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

# Ограничения на строки: они попадают в колонки хранилища, и бесконечная
# строка в событии — это либо ошибка клиента, либо попытка засорить хранилище.
ShortString = Annotated[str, Field(min_length=1, max_length=255)]
Url = Annotated[str, Field(min_length=1, max_length=2048)]

# Числа тоже ограничены сверху, и это не придирка: в аналитическом хранилище
# они лежат в UInt32, и значение больше 2^32-1 туда просто не влезет. Пропусти
# его API — ETL остановился бы на этой пачке навсегда: вставка падает, пачка
# не подтверждается, после перезапуска читается та же пачка.
UINT32_MAX = 2 ** 32 - 1
DAY_MS = 24 * 60 * 60 * 1000
# Отрезки времени внутри суток: и время на странице, и длительность фильма, и
# позиция в нём. Предел суток строже UInt32 и осмысленнее: событие о фильме
# длиной в неделю — это ошибка клиента, а не данные.
DurationMs = Annotated[int, Field(ge=0, le=DAY_MS)]
Counter = Annotated[int, Field(ge=0, le=UINT32_MAX)]


class EventType(StrEnum):
    """Типы событий, которые собирает сервис."""

    CLICK = 'click'
    PAGE_VIEW = 'page_view'
    QUALITY_CHANGED = 'quality_changed'
    VIDEO_COMPLETED = 'video_completed'
    SEARCH_FILTERS_APPLIED = 'search_filters_applied'


class ClientInfo(BaseModel):
    """Откуда пришло событие: по этим полям аналитика режет данные по платформам."""

    model_config = ConfigDict(extra='forbid')

    platform: Literal['web', 'ios', 'android', 'smart_tv']
    device: ShortString | None = None
    app_version: ShortString | None = None


class BaseEvent(BaseModel):
    """Общая часть любого события.

    `event_id` **обязателен и генерируется клиентом**, а не сервисом. Это
    единственное, по чему аналитика узнаёт повтор: событие может записаться в
    брокер, а ответ — не дойти (оборвалась сеть, истёк таймаут), и клиент
    отправит пачку заново. Подставляй сервис свой идентификатор, повтор стал бы
    новым событием, и просмотры посчитались бы дважды. По той же причине клиент
    обязан сохранять `event_id` между попытками: отправка пачки в брокер не
    транзакционна, часть событий может записаться, а часть — нет.

    `occurred_at` — время на стороне клиента и обязательно с часовым поясом:
    зрители в разных поясах, а сравнивать события нужно по одной шкале.
    Время приёма сервис проставит сам, расхождение с ним видно в аналитике.
    """

    model_config = ConfigDict(extra='forbid')

    event_id: UUID
    session_id: UUID
    occurred_at: AwareDatetime
    client: ClientInfo


class ClickEvent(BaseEvent):
    """Клик по элементу интерфейса: карточке фильма, трейлеру, категории (ФТ-1)."""

    event_type: Literal[EventType.CLICK]
    element_type: ShortString
    element_id: ShortString | None = None
    page: Url
    film_id: UUID | None = None


class PageViewEvent(BaseEvent):
    """Просмотр страницы и время, проведённое на ней (ФТ-2).

    Время присылается вместе с уходом со страницы, поэтому событие приходит
    позже самого просмотра — на порядок сортировки в аналитике это не влияет,
    там используется `occurred_at`.
    """

    event_type: Literal[EventType.PAGE_VIEW]
    page: Url
    referrer: Url | None = None
    duration_ms: DurationMs


class QualityChangedEvent(BaseEvent):
    """Смена качества видео, например 720p → 1080p (ФТ-3)."""

    event_type: Literal[EventType.QUALITY_CHANGED]
    film_id: UUID
    quality_from: ShortString
    quality_to: ShortString
    position_ms: DurationMs


class VideoCompletedEvent(BaseEvent):
    """Досмотр фильма (ФТ-3).

    `watched_ratio` — доля просмотренного, от 0 до 1. Именно по ней считаются
    недосмотренные фильмы: событие приходит и тогда, когда зритель бросил
    фильм, — тогда доля меньше единицы.
    """

    event_type: Literal[EventType.VIDEO_COMPLETED]
    film_id: UUID
    watched_ratio: float = Field(ge=0, le=1)
    duration_ms: DurationMs


class SearchFiltersAppliedEvent(BaseEvent):
    """Применение фильтров поиска (ФТ-3)."""

    event_type: Literal[EventType.SEARCH_FILTERS_APPLIED]
    query: Annotated[str, Field(max_length=512)] | None = None
    # Набор фильтров произвольный — он меняется вместе с интерфейсом, и
    # перечислять его здесь значило бы менять сервис после каждой правки формы.
    filters: dict[ShortString, ShortString] = Field(default_factory=dict, max_length=20)
    results_count: Counter


# Размеченное объединение: pydantic выбирает модель по значению event_type.
Event = Annotated[
    ClickEvent | PageViewEvent | QualityChangedEvent | VideoCompletedEvent | SearchFiltersAppliedEvent,
    Field(discriminator='event_type'),
]

# Адаптер разбирает по одному событию за раз: событие, не прошедшее проверку,
# не должно уносить с собой всю пачку (ФТ-6).
EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


class EventsRequest(BaseModel):
    """Пачка событий: клиент копит их и отправляет одним запросом (ФТ-4).

    События здесь — ещё не разобранные словари: каждое проверяется отдельно,
    чтобы одно испорченное не отменило остальные. Ограничение на размер пачки
    задаётся настройкой и проверяется в обработчике.
    """

    model_config = ConfigDict(extra='forbid')

    events: list[dict] = Field(min_length=1)
