"""Пайплайны ETL: какие данные из PostgreSQL в какой индекс Elasticsearch переносятся.

Каждый пайплайн описывает индекс, таблицу-источник, запрос документов по списку ID
и преобразование строки БД в документ. Логика переноса у всех пайплайнов общая
и живёт в etl.py.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from core.queries import FETCH_GENRES_BY_IDS, FETCH_MOVIES_BY_IDS, FETCH_PERSONS_BY_IDS
from core.schemas import (
    GENRES_INDEX,
    GENRES_INDEX_BODY,
    MOVIES_INDEX,
    MOVIES_INDEX_BODY,
    PERSONS_INDEX,
    PERSONS_INDEX_BODY,
)
from models.dataclasses import Genre, GenreDocument, Movie, Person, PersonDocument, PersonFilm


@dataclass(frozen=True)
class Pipeline:
    """Описание переноса одной сущности в свой индекс."""

    index: str
    index_body: Dict[str, Any]
    table: str
    # Запрос документов по списку ID, параметр %(ids)s. Если ID нет в выборке,
    # документ удаляется из индекса.
    query: str
    transform: Callable[[Dict[str, Any]], Dict[str, Any]]


def transform_row_to_movie(row: Dict[str, Any]) -> Movie:
    """Преобразует строку из БД в объект Movie.

    Args:
        row: Словарь с данными фильма из PostgreSQL

    Returns:
        Объект Movie с разделенными персонами по ролям
    """
    actors: List[Person] = []
    directors: List[Person] = []
    writers: List[Person] = []

    # Разделяем персон по ролям
    for person in row.get("persons", []):
        p = Person(id=person["id"], name=person["name"])
        role = person.get("role", "")
        if role == "actor":
            actors.append(p)
        elif role == "director":
            directors.append(p)
        elif role == "writer":
            writers.append(p)

    genres = [
        Genre(id=g["id"], name=g["name"])
        for g in row.get("genres", [])
    ]

    return Movie(
        id=row["id"],
        title=row["title"],
        description=row.get("description"),
        imdb_rating=row.get("imdb_rating"),
        creation_date=row.get("creation_date"),
        genres=genres,
        actors=actors,
        directors=directors,
        writers=writers,
    )


def movie_document(row: Dict[str, Any]) -> Dict[str, Any]:
    """Документ индекса movies."""
    return transform_row_to_movie(row).to_es_document()


def genre_document(row: Dict[str, Any]) -> Dict[str, Any]:
    """Документ индекса genres."""
    return GenreDocument(
        id=row["id"],
        name=row["name"],
        description=row.get("description"),
    ).to_es_document()


def person_document(row: Dict[str, Any]) -> Dict[str, Any]:
    """Документ индекса persons."""
    return PersonDocument(
        id=row["id"],
        full_name=row["full_name"],
        films=[PersonFilm(id=film["id"], roles=film["roles"]) for film in row["films"]],
    ).to_es_document()


MOVIES_PIPELINE = Pipeline(
    index=MOVIES_INDEX,
    index_body=MOVIES_INDEX_BODY,
    table="film_work",
    query=FETCH_MOVIES_BY_IDS,
    transform=movie_document,
)

GENRES_PIPELINE = Pipeline(
    index=GENRES_INDEX,
    index_body=GENRES_INDEX_BODY,
    table="genre",
    query=FETCH_GENRES_BY_IDS,
    transform=genre_document,
)

PERSONS_PIPELINE = Pipeline(
    index=PERSONS_INDEX,
    index_body=PERSONS_INDEX_BODY,
    table="person",
    query=FETCH_PERSONS_BY_IDS,
    transform=person_document,
)

PIPELINES = (MOVIES_PIPELINE, GENRES_PIPELINE, PERSONS_PIPELINE)
