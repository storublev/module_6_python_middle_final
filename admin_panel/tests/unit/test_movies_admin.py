"""Карточка фильма в админке: обложка из базы каталога, её загрузка и раздача."""

from uuid import uuid4

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from movies import admin as movies_admin
from movies import views
from movies.admin import FilmWorkAdmin, FilmWorkForm
from movies.models import FilmWork
from movies.posters import MAX_SIZE, image_type, read_upload

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 32
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
WEBP = b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'\x00' * 32


@pytest.fixture(autouse=True)
def no_loaded_posters(monkeypatch):
    """Таблиц каталога в тестовой SQLite нет: по умолчанию картинок в базе нет."""
    monkeypatch.setattr(movies_admin, 'poster_version', lambda film_id: None)


def test_preview_shows_poster_from_database_with_version(monkeypatch):
    """Превью — картинка из базы каталога, с версией в адресе: после замены не покажется старая из кеша."""
    monkeypatch.setattr(movies_admin, 'poster_version', lambda film_id: 1760000000)
    film = FilmWork(title='Star Wars')

    preview = FilmWorkAdmin(FilmWork, AdminSite()).poster_preview(film)

    assert f'src="/posters/{film.pk}.jpg?v=1760000000"' in preview


def test_film_without_poster_has_dash():
    """У фильма без обложки — прочерк, а не битая картинка."""
    assert FilmWorkAdmin(FilmWork, AdminSite()).poster_preview(FilmWork(title='Unknown')) == '—'


def test_film_form_has_no_links():
    """В карточке нет полей со ссылками на сторонние ресурсы — только загрузка файла."""
    fields = set(FilmWorkForm.base_fields)

    assert {'poster_file', 'remove_poster'} <= fields
    assert not {name for name in fields if 'url' in name}


@pytest.mark.parametrize(
    'head, expected',
    [(JPEG, 'image/jpeg'), (PNG, 'image/png'), (WEBP, 'image/webp'), (b'GIF89a...', 'image/gif'),
     (b'<html>', None), (b'%PDF-1.7', None)],
    ids=['jpeg', 'png', 'webp', 'gif', 'html', 'pdf'],
)
def test_image_type_by_signature(head, expected):
    """Тип картинки определяется по первым байтам, а не по имени и заголовкам от браузера."""
    assert image_type(head[:16]) == expected


def test_upload_reads_bytes_and_type():
    """Загруженная картинка читается целиком с типом по сигнатуре, даже если браузер назвал её иначе."""
    upload = SimpleUploadedFile('cover.png', JPEG, content_type='image/png')

    assert read_upload(upload) == (JPEG, 'image/jpeg')


@pytest.mark.parametrize(
    'content',
    [b'<script>alert(1)</script>', JPEG + b'\x00' * MAX_SIZE],
    ids=['not-an-image', 'too-big'],
)
def test_upload_rejects_non_images_and_large_files(content):
    """Не картинка и файл больше 2 МБ отклоняются понятной ошибкой формы."""
    with pytest.raises(ValidationError):
        read_upload(SimpleUploadedFile('cover.jpg', content, content_type='image/jpeg'))


def test_save_stores_uploaded_poster(monkeypatch):
    """Сохранение карточки с файлом кладёт картинку в базу; галочка «Удалить» — удаляет."""
    saved, deleted = [], []
    monkeypatch.setattr(movies_admin, 'save_poster', lambda *args: saved.append(args))
    monkeypatch.setattr(movies_admin, 'delete_poster', lambda film_id: deleted.append(film_id))
    monkeypatch.setattr(movies_admin.admin.ModelAdmin, 'save_model', lambda *args: None)
    admin = FilmWorkAdmin(FilmWork, AdminSite())
    film = FilmWork(title='Star Wars')

    class Form:
        cleaned_data = {'poster_file': (JPEG, 'image/jpeg'), 'remove_poster': False}

    admin.save_model(None, film, Form(), change=True)
    Form.cleaned_data = {'poster_file': None, 'remove_poster': True}
    admin.save_model(None, film, Form(), change=True)

    assert saved == [(film.pk, JPEG, 'image/jpeg')]
    assert deleted == [film.pk]


def test_poster_view_serves_image_with_cache(monkeypatch):
    """/posters/<id>.jpg отдаёт картинку из базы с её типом и долгим кешем."""
    monkeypatch.setattr(views, 'load_poster', lambda film_id: (JPEG, 'image/jpeg'))

    response = Client().get(f'/posters/{uuid4()}.jpg')

    assert (response.status_code, response['Content-Type'], response.content) == (200, 'image/jpeg', JPEG)
    assert 'max-age=86400' in response['Cache-Control']


def test_missing_poster_is_404(monkeypatch):
    """Нет картинки — 404, и страница кинотеатра покажет заглушку."""
    monkeypatch.setattr(views, 'load_poster', lambda film_id: None)

    assert Client().get(f'/posters/{uuid4()}.jpg').status_code == 404
    assert Client().get('/posters/not-a-uuid.jpg').status_code == 404
