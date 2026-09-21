"""Контракт событий: что сервис принимает, а что отклоняет."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from models.events import EVENT_ADAPTER, ClickEvent, PageViewEvent, VideoCompletedEvent

SESSION_ID = 'd3f1b4c2-51f9-4a1f-9f0e-2b6c1a7e4d11'
FILM_ID = 'b1a1d6f2-4f2a-4f0b-8f27-6b0a0e5f3f10'
CLIENT = {'platform': 'web'}
OCCURRED_AT = '2026-09-21T19:04:11+03:00'


def base(**fields) -> dict:
    return {
        'event_id': str(uuid4()),
        'session_id': SESSION_ID,
        'occurred_at': OCCURRED_AT,
        'client': CLIENT,
        **fields,
    }


def test_click_event_is_parsed() -> None:
    """Клик по элементу интерфейса разбирается как ClickEvent."""
    event = EVENT_ADAPTER.validate_python(
        base(event_type='click', element_type='film_card', page='/catalog', film_id=FILM_ID),
    )

    assert isinstance(event, ClickEvent)
    assert event.film_id == UUID(FILM_ID)
    assert event.element_id is None


def test_page_view_event_is_parsed() -> None:
    """Просмотр страницы разбирается вместе со временем, проведённым на ней."""
    event = EVENT_ADAPTER.validate_python(base(event_type='page_view', page='/catalog', duration_ms=51000))

    assert isinstance(event, PageViewEvent)
    assert event.duration_ms == 51000


def test_video_completed_event_is_parsed() -> None:
    """Досмотр фильма разбирается вместе с долей просмотра."""
    event = EVENT_ADAPTER.validate_python(
        base(event_type='video_completed', film_id=FILM_ID, watched_ratio=0.34, duration_ms=2140000),
    )

    assert isinstance(event, VideoCompletedEvent)
    assert event.watched_ratio == pytest.approx(0.34)


def test_event_without_event_id_is_rejected() -> None:
    """Событие без event_id не принимается: подставь его сервис — повтор запроса стал бы новым событием."""
    event = base(event_type='page_view', page='/', duration_ms=1)
    del event['event_id']

    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(event)


def test_unknown_event_type_is_rejected() -> None:
    """Событие неизвестного типа не принимается: контракт закрытый."""
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(base(event_type='like', film_id=FILM_ID))


def test_unknown_field_is_rejected() -> None:
    """Лишнее поле отклоняется: иначе в хранилище поедет то, чего нет в схеме."""
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(base(event_type='page_view', page='/', duration_ms=1, extra='нет такого'))


def test_user_id_in_body_is_rejected() -> None:
    """user_id из тела не принимается: клиент прислал бы чужой, его берут из токена."""
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(
            base(event_type='page_view', page='/', duration_ms=1, user_id=FILM_ID),
        )


def test_naive_occurred_at_is_rejected() -> None:
    """Время без часового пояса отклоняется: зрители в разных поясах."""
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(
            {
                'event_type': 'page_view',
                'event_id': str(uuid4()),
                'session_id': SESSION_ID,
                'occurred_at': '2026-09-21T19:04:11',
                'client': CLIENT,
                'page': '/',
                'duration_ms': 1,
            },
        )


def test_watched_ratio_out_of_range_is_rejected() -> None:
    """Доля просмотра больше единицы отклоняется."""
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(
            base(event_type='video_completed', film_id=FILM_ID, watched_ratio=1.5, duration_ms=10),
        )


def test_negative_duration_is_rejected() -> None:
    """Отрицательное время на странице отклоняется."""
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(base(event_type='page_view', page='/', duration_ms=-1))


def test_unknown_platform_is_rejected() -> None:
    """Платформа вне перечня отклоняется: по ней режутся данные в аналитике."""
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(
            {
                'event_type': 'page_view',
                'event_id': str(uuid4()),
                'session_id': SESSION_ID,
                'occurred_at': OCCURRED_AT,
                'client': {'platform': 'playstation'},
                'page': '/',
                'duration_ms': 1,
            },
        )


def test_search_filters_event_is_parsed() -> None:
    """Применение фильтров поиска разбирается вместе с самими фильтрами."""
    event = EVENT_ADAPTER.validate_python(
        base(
            event_type='search_filters_applied',
            query='нолан',
            filters={'genre': 'sci-fi', 'year_from': '2010'},
            results_count=12,
        ),
    )

    assert event.filters == {'genre': 'sci-fi', 'year_from': '2010'}
    assert event.results_count == 12


@pytest.mark.parametrize(
    ('event_type', 'field', 'value'),
    [
        ('page_view', 'duration_ms', 2 ** 32),
        ('quality_changed', 'position_ms', 2 ** 32),
        ('video_completed', 'duration_ms', 2 ** 32),
        ('search_filters_applied', 'results_count', 2 ** 32),
    ],
)
def test_number_beyond_the_storage_column_is_rejected(event_type, field, value) -> None:
    """Число, которое не влезет в колонку хранилища, не принимается.

    В аналитическом хранилище это UInt32. Пропусти такое значение API — ETL
    встал бы на этой пачке навсегда: вставка падает, смещения не
    подтверждаются, после перезапуска читается та же пачка.
    """
    defaults = {
        'page_view': {'page': '/'},
        'quality_changed': {'film_id': FILM_ID, 'quality_from': '720p', 'quality_to': '1080p'},
        'video_completed': {'film_id': FILM_ID, 'watched_ratio': 0.5},
        'search_filters_applied': {},
    }[event_type]

    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(base(event_type=event_type, **defaults, **{field: value}))
