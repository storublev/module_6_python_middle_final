"""ElasticStorage переводит нейтральный запрос сервиса в запрос Elasticsearch."""

from typing import Any

import pytest

from storage.base import FieldIn, SearchField, SearchRequest, TextQuery
from storage.elastic import ElasticStorage
from storage.resilience import BackoffPolicy


class FakeElastic:
    """Запоминает параметры поиска вместо обращения к Elasticsearch."""

    def __init__(self) -> None:
        self.search_params: dict[str, Any] = {}

    async def search(self, **params: Any) -> dict[str, Any]:
        self.search_params = params
        return {'hits': {'hits': []}}


async def search_fields(*fields: SearchField) -> list[str]:
    elastic = FakeElastic()
    storage = ElasticStorage(elastic, retry=BackoffPolicy(max_time=0, factor=0, max_value=0))

    await storage.search('movies', SearchRequest(fields=('id',), text=TextQuery('star', fields)))

    return elastic.search_params['query']['bool']['must'][0]['multi_match']['fields']


@pytest.mark.parametrize(
    'fields, expected',
    [
        pytest.param((SearchField('title', weight=3), SearchField('description')), ['title^3', 'description'],
                     id='weighted-and-default'),
        pytest.param((SearchField('title', weight=1.5),), ['title^1.5'], id='fractional-weight'),
        pytest.param((SearchField('full_name', weight=1),), ['full_name'], id='weight-one-omitted'),
    ],
)
async def test_field_weight_in_elastic_syntax(fields, expected):
    assert await search_fields(*fields) == expected


@pytest.mark.parametrize('weight', [0, -1])
def test_field_weight_must_be_positive(weight):
    with pytest.raises(ValueError):
        SearchField('title', weight=weight)


async def search_query(request: SearchRequest) -> dict[str, Any]:
    elastic = FakeElastic()
    storage = ElasticStorage(elastic, retry=BackoffPolicy(max_time=0, factor=0, max_value=0))

    await storage.search('movies', request)

    return elastic.search_params['query']


async def test_field_filter_becomes_terms():
    """Отбор по значениям поля переводится в terms — фильтр, не влияющий на релевантность."""
    request = SearchRequest(fields=('id',), filters=(FieldIn('access_level', ('public', 'subscription')),))

    query = await search_query(request)

    assert query['bool']['filter'] == [{'terms': {'access_level': ['public', 'subscription']}}]


async def test_field_filter_combines_with_text_search():
    """Отбор и поиск уживаются: искомое ищется, а выдача ограничивается отбором."""
    request = SearchRequest(
        fields=('id',),
        text=TextQuery('star', (SearchField('title'),)),
        filters=(FieldIn('access_level', ('public',)),),
    )

    query = await search_query(request)

    assert len(query['bool']['must']) == 1
    assert query['bool']['filter'] == [{'terms': {'access_level': ['public']}}]
