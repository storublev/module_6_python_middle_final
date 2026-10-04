"""Модели документов индекса persons."""

from models.base import IdModel


class PersonFilm(IdModel):
    """Фильм персоны и роли, в которых она в нём участвовала."""

    roles: list[str] = []


class Person(IdModel):
    full_name: str
    films: list[PersonFilm] = []
