"""Схемы ответов API.

Поля названы так, как их ждут клиенты по ТЗ (`uuid`, `genre`, `full_name`),
а читаются из моделей документов Elasticsearch (`id`, `genres`, `name`)
через validation_alias. Поэтому эндпоинты возвращают модели сервисов как есть.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID = Field(validation_alias='id')


class GenreSchema(ResponseSchema):
    name: str


class FilmPersonSchema(ResponseSchema):
    full_name: str = Field(validation_alias='name')


class FilmShortSchema(ResponseSchema):
    title: str
    imdb_rating: float | None


class FilmSchema(FilmShortSchema):
    description: str | None
    genre: list[GenreSchema] = Field(validation_alias='genres')
    actors: list[FilmPersonSchema]
    writers: list[FilmPersonSchema]
    directors: list[FilmPersonSchema]


class PersonFilmSchema(ResponseSchema):
    roles: list[str]


class PersonSchema(ResponseSchema):
    full_name: str
    films: list[PersonFilmSchema]
