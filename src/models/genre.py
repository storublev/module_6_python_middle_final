"""Модели документов индекса genres."""

from models.base import IdModel


class Genre(IdModel):
    name: str
    description: str | None = None
