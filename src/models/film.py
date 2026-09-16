"""Модели документов индекса movies."""

from enum import StrEnum

from models.base import IdModel


class AccessLevel(StrEnum):
    """Кому доступен фильм. Метку проставляет ETL, сервис контента её только читает.

    По какому правилу фильм становится подписочным (сейчас — вышел менее трёх
    лет назад), знает ETL: правило может смениться, и менять его придётся в
    одном месте, а не в каждом сервисе.
    """

    PUBLIC = 'public'
    SUBSCRIPTION = 'subscription'


class FilmGenre(IdModel):
    """Жанр внутри документа фильма."""

    name: str


class FilmPerson(IdModel):
    """Актёр, сценарист или режиссёр внутри документа фильма."""

    name: str


class FilmShort(IdModel):
    """Краткая информация о фильме — для списков и поиска."""

    title: str
    imdb_rating: float | None = None


class Film(FilmShort):
    """Полная информация о фильме."""

    # Тип — str, а не AccessLevel: если ETL заведёт новый уровень, документ
    # должен читаться и старой версией сервиса, а не ломать выдачу. Документы,
    # проиндексированные до появления метки, считаем публичными — иначе
    # каталог закрылся бы целиком.
    access_level: str = AccessLevel.PUBLIC
    description: str | None = None
    genres: list[FilmGenre] = []
    actors: list[FilmPerson] = []
    writers: list[FilmPerson] = []
    directors: list[FilmPerson] = []
