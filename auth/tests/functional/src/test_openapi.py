"""Документация OpenAPI: у каждого эндпоинта описаны ответы с ошибками."""

import httpx

from tests.functional.settings import settings

# Эндпоинты, которым access-токен не нужен вовсе или нужен необязательно:
# у них нет ответа not_authenticated.
PUBLIC = {
    '/auth/api/v1/signup',
    '/auth/api/v1/login',
    '/auth/api/v1/token/refresh',
    '/auth/api/v1/access/check',
    '/auth/api/v1/oauth/providers',
    '/auth/api/v1/oauth/{provider}/login',
    '/auth/api/v1/oauth/{provider}/callback',
}


async def get_spec() -> dict:
    async with httpx.AsyncClient(base_url=settings.service_url) as client:
        response = await client.get('/auth/api/openapi.json')
    assert response.status_code == 200
    return response.json()


async def test_every_endpoint_documents_errors() -> None:
    """Каждый эндпоинт описывает 503, а эндпоинты с access-токеном — ещё и все коды 401."""
    spec = await get_spec()

    for path, operations in spec['paths'].items():
        for method, operation in operations.items():
            responses = operation['responses']
            assert '503' in responses, f'{method} {path}'
            if path not in PUBLIC:
                codes = set(responses['401']['content']['application/json']['examples'])
                assert codes == {'not_authenticated', 'token_expired', 'token_invalid', 'token_revoked'}, path


async def test_throttled_endpoints_document_429() -> None:
    """Вход и регистрация описывают 429 too_many_requests с заголовком Retry-After."""
    spec = await get_spec()

    for path in ('/auth/api/v1/login', '/auth/api/v1/signup'):
        response = spec['paths'][path]['post']['responses']['429']
        assert set(response['content']['application/json']['examples']) == {'too_many_requests'}, path
        assert 'Retry-After' in response['headers'], path


async def test_bearer_security_scheme() -> None:
    """В документации есть схема авторизации Bearer: токен можно передать из Swagger UI."""
    spec = await get_spec()

    assert spec['components']['securitySchemes']['accessToken'] == {
        'type': 'http', 'scheme': 'bearer', 'description': 'access-токен из ответа на вход или обновление токенов',
    }
