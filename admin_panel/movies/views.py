"""Обложки фильмов из базы каталога: /posters/<film_id>.jpg.

Картинки лежат в content.film_poster (их загружает ETL), и отдавать их может
только тот, у кого есть доступ к базе каталога, — это админка. Страницы
кинотеатра ссылаются на этот адрес, а nginx кеширует ответы: Django
спрашивают один раз на картинку, а не на каждый показ каталога.
"""

from uuid import UUID

from django.http import Http404, HttpRequest, HttpResponse
from django.views.decorators.http import require_GET

from movies.models import FilmPoster

# Сутки в кеше браузера и nginx: обложка меняется редко, а новая загрузка
# меняет адрес в индексе только через ETL — день устаревания приемлем.
CACHE_SECONDS = 24 * 3600


def load_poster(film_id: UUID) -> tuple[bytes, str] | None:
    row = FilmPoster.objects.filter(film_work_id=film_id).values_list('content', 'content_type').first()
    return (bytes(row[0]), row[1]) if row else None


@require_GET
def poster(request: HttpRequest, film_id: UUID) -> HttpResponse:
    found = load_poster(film_id)
    if found is None:
        raise Http404('Poster not found')
    content, content_type = found
    response = HttpResponse(content, content_type=content_type)
    response['Cache-Control'] = f'public, max-age={CACHE_SECONDS}'
    return response
