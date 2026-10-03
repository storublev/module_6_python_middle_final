"""Схемы ответов API.

Поля названы так, как их ждут клиенты по ТЗ (`uuid`, `genre`, `full_name`),
а читаются из моделей документов Elasticsearch (`id`, `genres`, `name`)
через validation_alias. Поэтому эндпоинты возвращают модели сервисов как есть.
Описания и примеры полей попадают в документацию OpenAPI.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID = Field(validation_alias='id', description='Идентификатор',
                       examples=['3d825f60-9fff-4dfe-b294-1a45fa1e115d'])


class GenreSchema(ResponseSchema):
    """Жанр."""

    name: str = Field(description='Название жанра', examples=['Sci-Fi'])


class FilmPersonSchema(ResponseSchema):
    """Участник фильма: актёр, сценарист или режиссёр."""

    full_name: str = Field(validation_alias='name', description='Имя', examples=['Mark Hamill'])


class FilmShortSchema(ResponseSchema):
    """Фильм в списках и результатах поиска."""

    title: str = Field(description='Название', examples=['Star Wars: Episode IV - A New Hope'])
    imdb_rating: float | None = Field(description='Рейтинг IMDb, если известен', examples=[8.6])
    type: str | None = Field(
        default=None, description='Тип: movie — полнометражный фильм, tv_show — сериал', examples=['movie'],
    )
    poster_url: str | None = Field(
        default=None,
        description='Ссылка на обложку; null — обложки нет, клиент рисует заглушку',
        examples=['https://m.media-amazon.com/images/M/MV5B...@._V1_QL75_UX400_.jpg'],
    )


class FilmSchema(FilmShortSchema):
    """Полная информация о фильме."""

    description: str | None = Field(description='Описание', examples=['The Imperial Forces...'])
    imdb_id: str | None = Field(default=None, description='Идентификатор на IMDb', examples=['tt0076759'])
    genre: list[GenreSchema] = Field(validation_alias='genres', description='Жанры')
    actors: list[FilmPersonSchema] = Field(description='Актёры')
    writers: list[FilmPersonSchema] = Field(description='Сценаристы')
    directors: list[FilmPersonSchema] = Field(description='Режиссёры')


class PersonFilmSchema(ResponseSchema):
    """Фильм персоны и её роли в нём."""

    roles: list[str] = Field(description='Роли: actor, writer, director', examples=[['actor', 'writer']])


class PersonSchema(ResponseSchema):
    """Персона и фильмы, в которых она участвовала."""

    full_name: str = Field(description='Имя', examples=['George Lucas'])
    films: list[PersonFilmSchema] = Field(description='Фильмы персоны с ролями')


class ErrorSchema(BaseModel):
    """Ошибка: запрошенного нет или сервис временно не может ответить."""

    detail: str = Field(description='Причина')


def error_response(detail: str) -> dict[str, Any]:
    """Описание ответа с ошибкой для параметра `responses` роутера."""
    return {
        'model': ErrorSchema,
        'description': detail,
        'content': {'application/json': {'example': {'detail': detail}}},
    }
