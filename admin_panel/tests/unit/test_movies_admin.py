"""Карточка фильма в админке: превью обложки рядом с её адресом."""

from django.contrib.admin.sites import AdminSite

from movies.admin import FilmWorkAdmin
from movies.models import FilmWork

POSTER = 'https://m.media-amazon.com/images/M/poster._V1_QL75_UX400_.jpg'


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
