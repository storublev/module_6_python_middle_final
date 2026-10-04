"""Обложка, загруженная редактором: проверка файла и запись в базу каталога.

Файл кладётся сразу в content.film_poster — без ссылок и без скриптов. Тип
картинки определяется по первым байтам, а не по имени файла и не по заголовку
браузера: и то и другое присылает клиент, и подделать их ничего не стоит.
"""

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from movies.models import FilmPoster

# Обложка в карточке — 300–400 точек по ширине: двух мегабайт хватит с запасом.
MAX_SIZE = 2 * 1024 * 1024
SIGNATURES = (
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
)


def image_type(head: bytes) -> str | None:
    """Тип картинки по сигнатуре файла или None, если это не картинка."""
    for signature, content_type in SIGNATURES:
        if head.startswith(signature):
            return content_type
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


def read_upload(upload: UploadedFile) -> tuple[bytes, str]:
    """Байты и тип загруженной картинки.

    Raises:
        ValidationError: файл больше 2 МБ или не JPEG/PNG/WebP/GIF.
    """
    if upload.size > MAX_SIZE:
        raise ValidationError('Файл больше 2 МБ — обложке столько не нужно.')
    content = upload.read()
    content_type = image_type(content[:16])
    if content_type is None:
        raise ValidationError('Это не картинка: нужен JPEG, PNG, WebP или GIF.')
    return content, content_type


def save_poster(film_id: object, content: bytes, content_type: str) -> None:
    """Кладёт картинку в базу, заменяя прежнюю."""
    FilmPoster.objects.update_or_create(
        film_work_id=film_id,
        defaults={'content': content, 'content_type': content_type, 'fetched_at': timezone.now()},
    )


def delete_poster(film_id: object) -> None:
    FilmPoster.objects.filter(film_work_id=film_id).delete()


def poster_version(film_id: object) -> int | None:
    """Версия обложки (время загрузки) или None, если обложки нет.

    Версия идёт в адрес картинки: после замены браузер и nginx не покажут
    старую из кеша — тот же приём, что у ETL для индекса.
    """
    loaded = FilmPoster.objects.filter(film_work_id=film_id).values_list('fetched_at', flat=True).first()
    return int(loaded.timestamp()) if loaded else None
