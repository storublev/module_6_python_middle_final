"""Документы для индексов Elasticsearch и ответы API, которые из них ожидаются.

Документы строятся по схемам из `es_mapping.py` (dynamic: strict — лишнее
поле Elasticsearch не примет). У каждого документа случайный uuid, поэтому
тесты не зависят друг от друга.
"""

from collections.abc import Iterable
from typing import Any
from uuid import uuid4

Doc = dict[str, Any]

# Метки доступа, которые ETL проставляет фильмам.
PUBLIC = 'public'
SUBSCRIPTION = 'subscription'
# Типы фильмов каталога.
MOVIE = 'movie'
TV_SHOW = 'tv_show'


def new_id() -> str:
    return str(uuid4())


def make_ref(name: str) -> Doc:
    """Жанр или персона внутри документа фильма."""
    return {'id': new_id(), 'name': name}


def make_film(
    title: str = 'The Star',
    imdb_rating: float | None = 8.5,
    description: str | None = 'New World',
    genres: Iterable[Doc] = (),
    actors: Iterable[Doc] = (),
    writers: Iterable[Doc] = (),
    directors: Iterable[Doc] = (),
    access_level: str = PUBLIC,
    film_type: str = MOVIE,
    poster_url: str | None = 'https://m.media-amazon.com/images/M/poster._V1_QL75_UX400_.jpg',
    imdb_id: str | None = 'tt0076759',
) -> Doc:
    actors, writers = list(actors), list(writers)
    return {
        'id': new_id(),
        'title': title,
        'imdb_rating': imdb_rating,
        'description': description,
        'access_level': access_level,
        'type': film_type,
        'poster_url': poster_url,
        'imdb_id': imdb_id,
        'genres': list(genres),
        'actors': actors,
        'writers': writers,
        'directors': list(directors),
        'actors_names': [actor['name'] for actor in actors],
        'writers_names': [writer['name'] for writer in writers],
    }


def make_films(count: int, **fields: Any) -> list[Doc]:
    return [make_film(**fields) for _ in range(count)]


def make_genre(name: str = 'Action', description: str | None = None) -> Doc:
    return {'id': new_id(), 'name': name, 'description': description}


def make_person(full_name: str = 'Ann Smith', films: Iterable[tuple[Doc, Iterable[str]]] = ()) -> Doc:
    """Персона; films — пары (документ фильма, роли персоны в нём)."""
    return {
        'id': new_id(),
        'full_name': full_name,
        'films': [{'id': film['id'], 'roles': list(roles)} for film, roles in films],
    }


# Ответы API. Поля названы по ТЗ: uuid, genre, full_name.

def film_short(film: Doc) -> Doc:
    return {
        'uuid': film['id'],
        'title': film['title'],
        'imdb_rating': film['imdb_rating'],
        'type': film['type'],
        'poster_url': film['poster_url'],
    }


def film_full(film: Doc) -> Doc:
    def people(refs: list[Doc]) -> list[Doc]:
        return [{'uuid': ref['id'], 'full_name': ref['name']} for ref in refs]

    return {
        **film_short(film),
        'description': film['description'],
        'imdb_id': film['imdb_id'],
        'genre': [{'uuid': genre['id'], 'name': genre['name']} for genre in film['genres']],
        'actors': people(film['actors']),
        'writers': people(film['writers']),
        'directors': people(film['directors']),
    }


def genre_view(genre: Doc) -> Doc:
    return {'uuid': genre['id'], 'name': genre['name']}


def person_view(person: Doc) -> Doc:
    return {
        'uuid': person['id'],
        'full_name': person['full_name'],
        'films': [{'uuid': film['id'], 'roles': film['roles']} for film in person['films']],
    }
