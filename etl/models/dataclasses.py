"""Модели данных для представления фильмов."""

from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import date

from access import access_level


@dataclass
class Person:
    """Участник фильма (актёр, режиссёр, сценарист)."""

    id: str
    name: str
    role: Optional[str] = None


@dataclass
class Genre:
    """Жанр фильма."""

    id: str
    name: str


@dataclass
class Movie:
    """Фильм со всеми связанными данными."""

    id: str
    title: str
    description: Optional[str] = None
    imdb_rating: Optional[float] = None
    creation_date: Optional[date] = None
    genres: list[Genre] = field(default_factory=list)
    actors: list[Person] = field(default_factory=list)
    directors: list[Person] = field(default_factory=list)
    writers: list[Person] = field(default_factory=list)
    modified: Optional[str] = None

    def to_es_document(self) -> dict:
        """Преобразует фильм в документ для Elasticsearch."""
        return {
            "id": self.id,
            "imdb_rating": self.imdb_rating,
            "genres": [asdict(g) for g in self.genres],
            "title": self.title,
            "description": self.description,
            "directors": [
                {"id": p.id, "name": p.name}
                for p in self.directors
            ],
            "actors": [
                {"id": p.id, "name": p.name}
                for p in self.actors
            ],
            "writers": [
                {"id": p.id, "name": p.name}
                for p in self.writers
            ],
            "actors_names": [p.name for p in self.actors],
            "writers_names": [p.name for p in self.writers],
            "creation_date": self.creation_date.isoformat() if self.creation_date else None,
            "access_level": access_level(self.creation_date),
        }


@dataclass
class GenreDocument:
    """Жанр для индекса genres."""

    id: str
    name: str
    description: Optional[str] = None

    def to_es_document(self) -> dict:
        """Преобразует жанр в документ для Elasticsearch."""
        return asdict(self)


@dataclass
class PersonFilm:
    """Фильм персоны и её роли в этом фильме."""

    id: str
    roles: list[str] = field(default_factory=list)


@dataclass
class PersonDocument:
    """Персона для индекса persons."""

    id: str
    full_name: str
    films: list[PersonFilm] = field(default_factory=list)

    def to_es_document(self) -> dict:
        """Преобразует персону в документ для Elasticsearch."""
        return asdict(self)
