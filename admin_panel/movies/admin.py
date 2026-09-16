from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from movies.models import FilmWork, Genre, GenreFilmWork, Person, PersonFilmWork


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


@admin.register(FilmWork)
class FilmWorkAdmin(admin.ModelAdmin):
    inlines = (GenreFilmWorkInline, PersonFilmWorkInline)
    list_display = ('title', 'type', 'creation_date', 'rating', 'display_genres', 'modified')
    list_filter = ('type', 'genres')
    search_fields = ('title', 'description')
    list_per_page = 25

    def get_queryset(self, request: HttpRequest) -> QuerySet[FilmWork]:
        # Без prefetch жанры каждой строки — отдельный запрос: страница из 25
        # фильмов стоила бы 26 запросов вместо двух.
        return super().get_queryset(request).prefetch_related('genres')

    @admin.display(description=_('genres'))
    def display_genres(self, film_work: FilmWork) -> str:
        return ', '.join(genre.name for genre in film_work.genres.all())
