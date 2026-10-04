from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from movies.models import FilmKinopoisk, FilmPoster, FilmWork, Genre, GenreFilmWork, Person, PersonFilmWork


def poster_loaded(film_id: object) -> bool:
    """Есть ли у фильма картинка в базе — тогда превью показывает её, а не внешнюю ссылку."""
    return FilmPoster.objects.filter(film_work_id=film_id).exists()


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'modified')
    search_fields = ('name',)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'modified')
    search_fields = ('full_name',)


class GenreFilmWorkInline(admin.TabularInline):
    model = GenreFilmWork
    extra = 0
    autocomplete_fields = ('genre',)


class PersonFilmWorkInline(admin.TabularInline):
    model = PersonFilmWork
    extra = 0
    # Персон в каталоге десятки тысяч: выпадающий список со всеми ними
    # обрушил бы страницу фильма, поэтому поиск по мере ввода.
    autocomplete_fields = ('person',)


class FilmKinopoiskInline(admin.StackedInline):
    """Что нашёл Кинопоиск: только для чтения — данные обновляет ETL, а не редактор."""

    model = FilmKinopoisk
    extra = 0
    can_delete = False
    fields = ('found', 'kinopoisk_id', 'title_ru', 'description_ru', 'year', 'rating', 'rating_votes',
              'poster_url', 'fetched_at')
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


@admin.register(FilmWork)
class FilmWorkAdmin(admin.ModelAdmin):
    inlines = (GenreFilmWorkInline, PersonFilmWorkInline, FilmKinopoiskInline)
    list_display = ('title', 'type', 'creation_date', 'rating', 'display_genres', 'modified')
    list_filter = ('type', 'genres')
    search_fields = ('title', 'description')
    list_per_page = 25
    readonly_fields = ('poster_preview',)

    @admin.display(description=_('poster preview'))
    def poster_preview(self, film_work: FilmWork) -> str:
        # Превью рядом с полем ссылки: опечатку в адресе видно сразу, а не на
        # карточке фильма после переиндексации.
        if poster_loaded(film_work.pk):
            # Загруженная в базу картинка — та, что видят зрители.
            return format_html('<img src="/posters/{}.jpg" alt="" style="max-height: 240px">', film_work.pk)
        if not film_work.poster_url:
            return '—'
        return format_html('<img src="{}" alt="" style="max-height: 240px">', film_work.poster_url)

    def save_model(self, request: HttpRequest, obj: FilmWork, form: object, change: bool) -> None:
        # Редактор сменил ссылку на обложку — загруженная картинка устарела.
        # Она удаляется, зрители видят новую ссылку сразу, а следующий запуск
        # scripts/fetch_poster_images.py загрузит новую картинку в базу.
        if change and 'poster_url' in getattr(form, 'changed_data', ()):
            FilmPoster.objects.filter(film_work_id=obj.pk).delete()
        super().save_model(request, obj, form, change)

    def get_queryset(self, request: HttpRequest) -> QuerySet[FilmWork]:
        # Без prefetch жанры каждой строки — отдельный запрос: страница из 25
        # фильмов стоила бы 26 запросов вместо двух.
        return super().get_queryset(request).prefetch_related('genres')

    @admin.display(description=_('genres'))
    def display_genres(self, film_work: FilmWork) -> str:
        return ', '.join(genre.name for genre in film_work.genres.all())
