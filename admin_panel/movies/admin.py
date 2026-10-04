from django import forms
from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from movies.models import FilmKinopoisk, FilmWork, Genre, GenreFilmWork, Person, PersonFilmWork
from movies.posters import delete_poster, poster_version, read_upload, save_poster


class FilmWorkForm(forms.ModelForm):
    """Карточка фильма с загрузкой обложки: файл сразу ложится в базу каталога."""

    poster_file = forms.FileField(
        label=_('poster'), required=False,
        help_text='JPEG, PNG, WebP или GIF до 2 МБ. Заменяет текущую обложку.',
        widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp,image/gif'}),
    )
    remove_poster = forms.BooleanField(label='Удалить обложку', required=False)

    class Meta:
        model = FilmWork
        fields = '__all__'

    def clean_poster_file(self) -> tuple[bytes, str] | None:
        upload = self.cleaned_data.get('poster_file')
        return read_upload(upload) if upload else None


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
              'fetched_at')
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


@admin.register(FilmWork)
class FilmWorkAdmin(admin.ModelAdmin):
    form = FilmWorkForm
    inlines = (GenreFilmWorkInline, PersonFilmWorkInline, FilmKinopoiskInline)
    list_display = ('title', 'type', 'creation_date', 'rating', 'display_genres', 'modified')
    list_filter = ('type', 'genres')
    search_fields = ('title', 'description')
    list_per_page = 25
    readonly_fields = ('poster_preview',)

    @admin.display(description=_('poster preview'))
    def poster_preview(self, film_work: FilmWork) -> str:
        # Та самая картинка, что видят зрители: из базы каталога.
        version = poster_version(film_work.pk) if film_work.pk else None
        if version is None:
            return '—'
        return format_html(
            '<img src="/posters/{}.jpg?v={}" alt="" style="max-height: 240px">', film_work.pk, version,
        )

    def save_model(self, request: HttpRequest, obj: FilmWork, form: forms.ModelForm, change: bool) -> None:
        # Сохранение фильма двигает modified, и ETL по журналу аудита сам
        # переиндексирует документ — с новой обложкой или без неё.
        super().save_model(request, obj, form, change)
        image = form.cleaned_data.get('poster_file')
        if image:
            save_poster(obj.pk, *image)
        elif form.cleaned_data.get('remove_poster'):
            delete_poster(obj.pk)

    def get_queryset(self, request: HttpRequest) -> QuerySet[FilmWork]:
        # Без prefetch жанры каждой строки — отдельный запрос: страница из 25
        # фильмов стоила бы 26 запросов вместо двух.
        return super().get_queryset(request).prefetch_related('genres')

    @admin.display(description=_('genres'))
    def display_genres(self, film_work: FilmWork) -> str:
        return ', '.join(genre.name for genre in film_work.genres.all())
