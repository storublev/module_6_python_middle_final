"""Отправка ошибок в Sentry: что уезжает наружу, а что остаётся здесь.

Проверяется не сам Sentry, а наши настройки: SDK подменяется приёмником в
памяти, в сервисе вызывается настоящее исключение, и тест смотрит, что попало
в подготовленное событие.
"""

from http import HTTPStatus
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from api.dependencies import build_services
from core.config import Settings
from core.sentry import configure_sentry
from main import create_app
from services.content import BookmarkService, LikeService, ReviewService
from storage.base import ReviewStorage
from tests.unit.conftest import API, REQUEST_ID, REQUEST_ID_HEADER
from tests.unit.fakes import InMemoryBookmarkStorage, InMemoryLikeStorage, ReadyHealthCheck

# Текст, который ни при каких условиях не должен попасть в мониторинг.
SECRET_REVIEW = 'ОченьЛичнаяРецензияКотораяНеДолжнаУтечь'
FILM_ID = UUID('b1a1d6f2-4f2a-4f0b-8f27-6b0a0e5f3f10')


class CapturingTransport(Transport):
    """Транспорт, который никуда не отправляет, а складывает события в список.

    Подкласс `Transport`, а не функция: функции-транспорты в SDK объявлены
    устаревшими. Событие сюда приходит уже собранным — ровно в том виде, в
    каком ушло бы по сети.
    """

    def __init__(self, events: list[dict]) -> None:
        super().__init__()
        self._events = events

    def capture_envelope(self, envelope) -> None:
        for item in envelope.items:
            payload = item.payload.json
            if payload is not None and 'exception' in payload:
                self._events.append(payload)

    def flush(self, *args, **kwargs) -> None:
        """Отправлять нечего: всё уже в списке."""

    def kill(self) -> None:
        """Закрывать тоже нечего."""


class BrokenReviewStorage(ReviewStorage):
    """Хранилище, которое падает неожиданно — как это бывает при настоящей аварии."""

    async def add_review(self, film_id, user_id, text, rating):
        # Ошибка не из нашего контракта: её никто не ловит, и она уходит в Sentry.
        raise TypeError('неожиданная ошибка внутри хранилища')

    async def get_review(self, review_id):
        raise NotImplementedError

    async def delete_review(self, review_id):
        raise NotImplementedError

    async def vote(self, review_id, user_id, useful):
        raise NotImplementedError

    async def list_reviews(self, film_id, sort, page, size):
        raise NotImplementedError


@pytest.fixture
def captured_events(settings: Settings, monkeypatch, make_token):
    """Приложение с включённым Sentry, чей транспорт заменён списком в памяти."""
    events: list[dict] = []

    services = build_services(
        likes=LikeService(InMemoryLikeStorage()),
        reviews=ReviewService(BrokenReviewStorage()),
        bookmarks=BookmarkService(InMemoryBookmarkStorage()),
        health=ReadyHealthCheck(),
    )
    config = settings.model_copy(update={'sentry_dsn': 'http://unit@localhost:9999/1'})

    import sentry_sdk

    original_init = sentry_sdk.init

    def init_with_capture(**options):
        options['transport'] = CapturingTransport(events)
        return original_init(**options)

    monkeypatch.setattr(sentry_sdk, 'init', init_with_capture)
    configure_sentry(config.sentry_dsn, config.project_name, config.sentry_environment)

    app = create_app(config, services=services)
    token = make_token(uuid4())
    with TestClient(app, headers={REQUEST_ID_HEADER: REQUEST_ID}, raise_server_exceptions=False) as client:
        response = client.post(
            f'{API}/films/{FILM_ID}/reviews',
            json={'text': SECRET_REVIEW, 'rating': 9},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    yield events
    # Клиент Sentry глобальный: выключаем его, чтобы соседние тесты не ловили
    # чужие события.
    with patch.object(sentry_sdk, 'init', original_init):
        sentry_sdk.init(dsn='')


def test_unhandled_error_reaches_sentry(captured_events):
    """Неотловленное исключение действительно уходит в мониторинг со стектрейсом."""
    assert len(captured_events) == 1
    values = captured_events[0]['exception']['values']
    assert values[-1]['type'] == 'TypeError'
    assert values[-1]['stacktrace']['frames']


def test_review_text_does_not_leak_to_sentry(captured_events):
    """Текст рецензии не уезжает в Sentry ни в каком виде.

    Главная ловушка здесь — снимок локальных переменных: `send_default_pii` и
    `max_request_body_size` его не выключают, а тело запроса лежит в
    переменных того кадра, где возникло исключение.
    """
    assert SECRET_REVIEW not in str(captured_events[0])


def test_stack_frames_carry_no_local_variables(captured_events):
    """Ни у одного кадра стека нет снимка локальных переменных.

    Проверяется именно отсутствие `vars`, а не отсутствие конкретного текста:
    завтра в переменных окажется что-то другое, а правило останется тем же.
    """
    frames = captured_events[0]['exception']['values'][-1]['stacktrace']['frames']

    assert frames, 'стектрейс не должен быть пустым — по нему ищут причину'
    assert all('vars' not in frame for frame in frames)


def test_request_body_is_not_attached(captured_events):
    """Тело запроса не прикладывается к событию отдельным полем."""
    request = captured_events[0].get('request', {})

    assert not request.get('data')
