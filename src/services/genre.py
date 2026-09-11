from functools import lru_cache

from elasticsearch import AsyncElasticsearch
from fastapi import Depends

from db.elastic import get_elastic
from models.genre import Genre
from services.base import BaseService, Pagination


class GenreService(BaseService[Genre]):
    index = 'genres'
    model = Genre

    async def get_list(self, pagination: Pagination) -> list[Genre]:
        """Список жанров в алфавитном порядке."""
        return await self._search(Genre, pagination, sort=[{'name.raw': {'order': 'asc'}}])


@lru_cache()
def get_genre_service(
    elastic: AsyncElasticsearch = Depends(get_elastic),
) -> GenreService:
    return GenreService(elastic)
