"""Модели каталога — отражение схемы content, общей с ETL и сервисом контента.

Таблицы создаёт не Django, а дамп базы фильмов из репозитория ETL, поэтому
модели объявлены `managed = False`: миграций у приложения нет и `migrate` их
не трогает. Схему меняет ETL — один владелец на таблицу, иначе миграции Django
и дамп разошлись бы. Нужная схема подставляется путём поиска (search_path) в
настройках базы.

ETL следит за колонкой modified и переносит изменения в Elasticsearch, поэтому
правка фильма в админке доезжает до выдачи сервиса контента без ручных действий.
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedMixin(models.Model):
    created = models.DateTimeField(_('created'), auto_now_add=True)
    modified = models.DateTimeField(_('modified'), auto_now=True)

    class Meta:
        abstract = True


class UUIDMixin(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class Genre(UUIDMixin, TimeStampedMixin):
    name = models.CharField(_('genre'), max_length=255, unique=True)
    description = models.TextField(_('description'), blank=True)

    def __str__(self) -> str:
        return self.name

    class Meta:
        managed = False
        db_table = 'genre'
        verbose_name = _('genre')
        verbose_name_plural = _('genres')
        ordering = ('name',)


class Person(UUIDMixin, TimeStampedMixin):
    full_name = models.CharField(_('full name'), max_length=255)

    def __str__(self) -> str:
        return self.full_name

    class Meta:
        managed = False
        db_table = 'person'
        verbose_name = _('person')
        verbose_name_plural = _('persons')
        ordering = ('full_name',)


class FilmWorkTypes(models.TextChoices):
    MOVIE = 'movie', _('movie')
    TV_SHOW = 'tv_show', _('tv show')


class PersonRoles(models.TextChoices):
    ACTOR = 'actor', _('actor')
    DIRECTOR = 'director', _('director')
    WRITER = 'writer', _('writer')


class FilmWork(UUIDMixin, TimeStampedMixin):
    title = models.CharField(_('title'), max_length=255)
    description = models.TextField(_('description'), blank=True)
    # Дата выхода решает, доступен ли фильм по подписке: ETL помечает фильмы
    # моложе трёх лет access_level=subscription.
    creation_date = models.DateField(_('creation date'), blank=True, null=True)
    rating = models.FloatField(_('rating'), blank=True, null=True)
    type = models.CharField(_('type'), max_length=7, choices=FilmWorkTypes.choices, default=FilmWorkTypes.MOVIE)
    # Обложка — ссылка на картинку у источника, саму картинку каталог не
    # хранит (ADR-25). Колонки заводит ETL при старте (etl/sql/catalog_extensions.sql),
    # первые значения он же загружает из posters.csv; здесь редактор их правит.
    poster_url = models.URLField(_('poster'), max_length=512, blank=True, null=True)
    imdb_id = models.CharField(_('IMDb id'), max_length=16, blank=True, null=True)
    genres = models.ManyToManyField(Genre, through='GenreFilmWork', verbose_name=_('genres'))
    persons = models.ManyToManyField(Person, through='PersonFilmWork', verbose_name=_('persons'))

    def __str__(self) -> str:
        return self.title

    class Meta:
        managed = False
        db_table = 'film_work'
        verbose_name = _('film')
        verbose_name_plural = _('films')
        ordering = ('-creation_date', 'title')


class GenreFilmWork(UUIDMixin):
    genre = models.ForeignKey(Genre, on_delete=models.CASCADE, verbose_name=_('genre'))
    film_work = models.ForeignKey(FilmWork, on_delete=models.CASCADE, verbose_name=_('film'))
    created = models.DateTimeField(_('created'), auto_now_add=True)

    def __str__(self) -> str:
        return self.genre.name

    class Meta:
        managed = False
        db_table = 'genre_film_work'
        verbose_name = _('film genre')
        verbose_name_plural = _('film genres')


class PersonFilmWork(UUIDMixin):
    person = models.ForeignKey(Person, on_delete=models.CASCADE, verbose_name=_('person'))
    film_work = models.ForeignKey(FilmWork, on_delete=models.CASCADE, verbose_name=_('film'))
    role = models.CharField(_('role'), max_length=50, choices=PersonRoles.choices)
    created = models.DateTimeField(_('created'), auto_now_add=True)

    def __str__(self) -> str:
        return f'{self.person.full_name} — {self.get_role_display()}'

    class Meta:
        managed = False
        db_table = 'person_film_work'
        verbose_name = _('film person')
        verbose_name_plural = _('film persons')
