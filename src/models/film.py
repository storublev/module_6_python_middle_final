"""Модели документов индекса movies."""

from models.base import IdModel


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

    description: str | None = None
    genres: list[FilmGenre] = []
    actors: list[FilmPerson] = []
    writers: list[FilmPerson] = []
    directors: list[FilmPerson] = []
