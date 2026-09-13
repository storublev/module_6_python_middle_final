"""Документация API: сервис отвечает и отдаёт схему OpenAPI."""

from http import HTTPStatus

from tests.functional.settings import settings


async def test_openapi_schema_available(http_session):
    async with http_session.get(f'{settings.service_url}/api/openapi.json') as response:
        schema = await response.json()

    assert response.status == HTTPStatus.OK
    assert schema['openapi'].startswith('3.')
