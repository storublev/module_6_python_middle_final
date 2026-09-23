from datetime import date
from typing import List, Optional

import orjson
from pydantic import BaseModel


def orjson_dumps(v, *, default):
    return orjson.dumps(v, default=default).decode()


class Base(BaseModel):
    id: str

    class Config:
        json_loads = orjson.loads
        json_dumps = orjson_dumps


class PersonBase(Base):
    full_name: str


class Person(PersonBase):
    role: List[str] = []
    film_ids: List[str] = []


class Genre(Base):
    name: str


class Film(Base):
    title: str
    description: str = None
    creation_date: date = None
    imdb_rating: Optional[float] = None
    genres: List[Genre] = []
    actors: List[PersonBase] = []
    directors: List[PersonBase] = []
    writers: List[PersonBase] = []
