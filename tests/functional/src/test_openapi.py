"""Документация API: схема OpenAPI и страница Swagger UI."""

from http import HTTPStatus

import pytest

from tests.functional.settings import settings

ENDPOINTS = [
    '/api/v1/films',
    '/api/v1/films/search',
    '/api/v1/films/{film_id}',
    '/api/v1/genres',
    '/api/v1/genres/{genre_id}',
    '/api/v1/persons',
    '/api/v1/persons/search',
    '/api/v1/persons/{person_id}',
    '/api/v1/persons/{person_id}/film',
]
DETAILS_ENDPOINTS = [endpoint for endpoint in ENDPOINTS if endpoint.endswith('_id}')]

ERROR_SCHEMA_REF = '#/components/schemas/ErrorSchema'


@pytest.fixture(scope='module')
async def openapi(http_session):
    async with http_session.get(f'{settings.service_url}/api/openapi.json') as response:
        assert response.status == HTTPStatus.OK
        return await response.json()


def error_schema_ref(operation: dict, status: HTTPStatus) -> str:
    return operation['responses'][str(status.value)]['content']['application/json']['schema']['$ref']


async def test_openapi_schema_available(openapi):
    assert openapi['openapi'].startswith('3.')
    assert openapi['info']['description']


async def test_swagger_ui_available(http_session):
    async with http_session.get(f'{settings.service_url}/api/openapi') as response:
        assert response.status == HTTPStatus.OK
        assert response.content_type == 'text/html'


async def test_all_endpoints_documented(openapi):
    assert sorted(openapi['paths']) == sorted(ENDPOINTS)
    for path, operations in openapi['paths'].items():
        assert operations['get']['summary'], path


async def test_tags_described(openapi):
    assert {tag['name']: bool(tag['description']) for tag in openapi['tags']} == {
        'films': True, 'genres': True, 'persons': True,
    }


@pytest.mark.parametrize('path', ENDPOINTS)
async def test_service_unavailable_documented(openapi, path):
    operation = openapi['paths'][path]['get']

    assert error_schema_ref(operation, HTTPStatus.SERVICE_UNAVAILABLE) == ERROR_SCHEMA_REF


@pytest.mark.parametrize('path', DETAILS_ENDPOINTS)
async def test_not_found_documented(openapi, path):
    operation = openapi['paths'][path]['get']

    assert error_schema_ref(operation, HTTPStatus.NOT_FOUND) == ERROR_SCHEMA_REF


@pytest.mark.parametrize('schema', ['FilmSchema', 'FilmShortSchema', 'GenreSchema', 'PersonSchema'])
async def test_response_fields_described(openapi, schema):
    properties = openapi['components']['schemas'][schema]['properties']

    assert all(field.get('description') for field in properties.values()), properties
