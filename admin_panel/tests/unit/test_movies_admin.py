"""Карточка фильма в админке: превью обложки рядом с её адресом."""

from uuid import uuid4

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import Client

from movies import admin as movies_admin
from movies import views
from movies.admin import FilmWorkAdmin
from movies.models import FilmWork

POSTER = 'https://m.media-amazon.com/images/M/poster._V1_QL75_UX400_.jpg'


@pytest.fixture(autouse=True)
def no_loaded_posters(monkeypatch):
    """Таблиц каталога в тестовой SQLite нет: по умолчанию картинок в базе нет."""
    monkeypatch.setattr(movies_admin, 'poster_loaded', lambda film_id: False)


def test_poster_preview_shows_image():
    """Обложка с адресом показывается картинкой: опечатку в ссылке редактор видит сразу."""
    admin = FilmWorkAdmin(FilmWork, AdminSite())

    preview = admin.poster_preview(FilmWork(title='Star Wars', poster_url=POSTER))

    assert f'<img src="{POSTER}"' in preview


def test_poster_preview_escapes_address():
    """Адрес из базы экранируется: кавычка в ссылке не превратится в разметку страницы."""
    admin = FilmWorkAdmin(FilmWork, AdminSite())

    preview = admin.poster_preview(FilmWork(title='X', poster_url='https://e.com/a.jpg" onerror="alert(1)'))

    assert 'onerror="' not in preview


def test_film_without_poster_has_dash():
    """У фильма без обложки — прочерк, а не битая картинка."""
    admin = FilmWorkAdmin(FilmWork, AdminSite())

    assert admin.poster_preview(FilmWork(title='Unknown')) == '—'


def test_loaded_poster_is_previewed_from_catalog(monkeypatch):
    """Если картинка уже в базе, превью показывает её — ту, что видят зрители."""
    monkeypatch.setattr(movies_admin, 'poster_loaded', lambda film_id: True)
    film = FilmWork(title='Star Wars', poster_url=POSTER)

    preview = FilmWorkAdmin(FilmWork, AdminSite()).poster_preview(film)

    assert f'src="/posters/{film.pk}.jpg"' in preview


def test_poster_view_serves_image_with_cache(monkeypatch):
    """/posters/<id>.jpg отдаёт картинку из базы с её типом и долгим кешем."""
    monkeypatch.setattr(views, 'load_poster', lambda film_id: (b'\xff\xd8jpeg', 'image/jpeg'))

    response = Client().get(f'/posters/{uuid4()}.jpg')

    assert (response.status_code, response['Content-Type'], response.content) == (200, 'image/jpeg', b'\xff\xd8jpeg')
    assert 'max-age=86400' in response['Cache-Control']


def test_missing_poster_is_404(monkeypatch):
    """Нет картинки — 404, и страница кинотеатра покажет заглушку."""
    monkeypatch.setattr(views, 'load_poster', lambda film_id: None)

    assert Client().get(f'/posters/{uuid4()}.jpg').status_code == 404
    assert Client().get('/posters/not-a-uuid.jpg').status_code == 404
