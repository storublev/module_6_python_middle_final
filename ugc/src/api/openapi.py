"""Документация OpenAPI.

У FastAPI она берётся из подписей обработчиков; Flask такого не умеет, поэтому
спецификация собирается здесь — но из тех же моделей pydantic, что проверяют
запросы. Так документация не расходится с кодом: изменилось поле события —
изменилась и схема в документации.

Правило проекта: у каждого эндпоинта описаны все ответы, включая ошибочные, и
у каждого тела запроса есть пример.
"""

from http import HTTPStatus
from typing import Any

from flask import Blueprint, jsonify

from models.events import EVENT_ADAPTER, EventsRequest

router = Blueprint('openapi', __name__)

# Схемы моделей ссылаются друг на друга через $ref; по умолчанию pydantic
# кладёт их в $defs, а OpenAPI ждёт их в components/schemas.
REF_TEMPLATE = '#/components/schemas/{model}'

ERROR_SCHEMA = {
    'type': 'object',
    'required': ['code', 'detail'],
    'properties': {
        'code': {'type': 'string', 'description': 'Машиночитаемый код ошибки', 'example': 'token_expired'},
        'detail': {'type': 'string', 'description': 'Пояснение для журнала', 'example': 'Access token has expired'},
    },
}

COLLECT_RESULT_SCHEMA = {
    'type': 'object',
    'required': ['accepted', 'rejected'],
    'properties': {
        'accepted': {'type': 'integer', 'description': 'Сколько событий принято и записано в брокер', 'example': 19},
        'rejected': {
            'type': 'array',
            'description': 'События, не прошедшие проверку контракта; остальные из той же пачки приняты',
            'items': {
                'type': 'object',
                'required': ['index', 'code', 'detail'],
                'properties': {
                    'index': {'type': 'integer', 'description': 'Место события в присланной пачке', 'example': 3},
                    'code': {'type': 'string', 'example': 'invalid_event'},
                    'detail': {'type': 'string', 'example': 'watched_ratio: Input should be less than or equal to 1'},
                },
            },
        },
    },
}

REQUEST_EXAMPLE = {
    'events': [
        {
            'event_type': 'click',
            'event_id': '2a4f2f4e-4b7d-4a3a-9c1e-8f4b6a2d1c00',
            'session_id': 'd3f1b4c2-51f9-4a1f-9f0e-2b6c1a7e4d11',
            'occurred_at': '2026-09-21T19:04:11+03:00',
            'client': {'platform': 'web', 'device': 'Chrome 140', 'app_version': '2.14.0'},
            'element_type': 'film_card',
            'element_id': 'recommended-3',
            'page': '/catalog/drama',
            'film_id': 'b1a1d6f2-4f2a-4f0b-8f27-6b0a0e5f3f10',
        },
        {
            'event_type': 'page_view',
            'session_id': 'd3f1b4c2-51f9-4a1f-9f0e-2b6c1a7e4d11',
            'occurred_at': '2026-09-21T19:05:02+03:00',
            'client': {'platform': 'web'},
            'page': '/film/b1a1d6f2-4f2a-4f0b-8f27-6b0a0e5f3f10',
            'referrer': '/catalog/drama',
            'duration_ms': 51000,
        },
        {
            'event_type': 'video_completed',
            'session_id': 'd3f1b4c2-51f9-4a1f-9f0e-2b6c1a7e4d11',
            'occurred_at': '2026-09-21T20:41:38+03:00',
            'client': {'platform': 'smart_tv', 'device': 'LG WebOS'},
            'film_id': 'b1a1d6f2-4f2a-4f0b-8f27-6b0a0e5f3f10',
            'watched_ratio': 0.34,
            'duration_ms': 2140000,
        },
    ],
}


def _error(description: str, code: str, detail: str) -> dict[str, Any]:
    """Ответ с ошибкой: у каждого свой пример, иначе документация не подсказывает."""
    return {
        'description': description,
        'content': {
            'application/json': {
                'schema': {'$ref': '#/components/schemas/Error'},
                'example': {'code': code, 'detail': detail},
            },
        },
    }


def build_spec() -> dict[str, Any]:
    """Собирает спецификацию OpenAPI из моделей сервиса."""
    event_schema = EVENT_ADAPTER.json_schema(ref_template=REF_TEMPLATE)
    schemas: dict[str, Any] = event_schema.pop('$defs', {})
    request_schema = EventsRequest.model_json_schema(ref_template=REF_TEMPLATE)
    schemas.update(request_schema.pop('$defs', {}))
    # Событие в пачке приходит словарём и проверяется по одному, но клиенту
    # нужна настоящая схема, а не «любой объект».
    request_schema['properties']['events']['items'] = event_schema
    schemas['Error'] = ERROR_SCHEMA
    schemas['CollectResult'] = COLLECT_RESULT_SCHEMA
    schemas['EventsRequest'] = request_schema

    return {
        'openapi': '3.1.0',
        'info': {
            'title': 'Сервис сбора пользовательских действий',
            'version': '1.0.0',
            'description': (
                'Принимает события онлайн-кинотеатра — клики, просмотры страниц и события плеера — '
                'и складывает их в Kafka, откуда ETL переносит их в аналитическое хранилище.\n\n'
                'События отправляются пачками: клиент копит их и шлёт одним запросом не реже '
                'раза в 10 секунд. Пользователь определяется по access-токену сервиса '
                'авторизации, поэтому `user_id` в теле не передаётся — он будет взят из токена.'
            ),
        },
        'servers': [{'url': '/ugc/api/v1', 'description': 'Через шлюз кинотеатра'}],
        'components': {
            'schemas': schemas,
            'securitySchemes': {
                'bearer': {
                    'type': 'http',
                    'scheme': 'bearer',
                    'bearerFormat': 'JWT',
                    'description': 'Access-токен сервиса авторизации',
                },
            },
        },
        'paths': {
            '/events': {
                'post': {
                    'summary': 'Принять пачку событий',
                    'description': (
                        'Каждое событие проверяется отдельно: непрошедшее контракт не отменяет '
                        'остальные. Ответ 202 значит, что события записаны в брокер, — в '
                        'аналитическом хранилище они появятся в течение нескольких минут.'
                    ),
                    'security': [{'bearer': []}],
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {'$ref': '#/components/schemas/EventsRequest'},
                                'example': REQUEST_EXAMPLE,
                            },
                        },
                    },
                    'responses': {
                        str(HTTPStatus.ACCEPTED): {
                            'description': 'Принято хотя бы одно событие',
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/CollectResult'},
                                    'example': {'accepted': 3, 'rejected': []},
                                },
                            },
                        },
                        str(HTTPStatus.BAD_REQUEST): _error(
                            'Тело запроса не разобрано или запрос пришёл мимо шлюза',
                            'request_id_required',
                            'X-Request-Id header is required; it is set by the gateway',
                        ),
                        str(HTTPStatus.UNAUTHORIZED): _error(
                            'Токена нет, он истёк или не принят',
                            'token_expired',
                            'Access token has expired',
                        ),
                        str(HTTPStatus.REQUEST_ENTITY_TOO_LARGE): _error(
                            'В пачке больше событий, чем сервис принимает за раз',
                            'batch_too_large',
                            'At most 200 events are accepted per request',
                        ),
                        str(HTTPStatus.UNPROCESSABLE_ENTITY): {
                            'description': 'Не принято ни одного события: все не прошли проверку',
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/CollectResult'},
                                    'example': {
                                        'accepted': 0,
                                        'rejected': [
                                            {
                                                'index': 0,
                                                'code': 'invalid_event',
                                                'detail': "event_type: Input tag 'like' found using 'event_type' "
                                                          'does not match any of the expected tags',
                                            },
                                        ],
                                    },
                                },
                            },
                        },
                        str(HTTPStatus.SERVICE_UNAVAILABLE): _error(
                            'Брокер не принял события; клиенту следует повторить запрос',
                            'queue_unavailable',
                            'Event queue is unavailable, retry later',
                        ),
                        str(HTTPStatus.INTERNAL_SERVER_ERROR): _error(
                            'Непредвиденная ошибка сервиса',
                            'internal_error',
                            'Internal server error',
                        ),
                    },
                },
            },
            '/health': {
                'get': {
                    'summary': 'Жив ли процесс',
                    'description': 'Используется healthcheck контейнера; заголовок X-Request-Id не требуется.',
                    'responses': {
                        str(HTTPStatus.OK): {
                            'description': 'Сервис жив',
                            'content': {'application/json': {'example': {'status': 'ok'}}},
                        },
                    },
                },
            },
            '/ready': {
                'get': {
                    'summary': 'Готов ли сервис принимать события',
                    'description': 'Проверяет соединение с брокером: без него принимать события бессмысленно.',
                    'responses': {
                        str(HTTPStatus.OK): {
                            'description': 'Соединение с брокером есть',
                            'content': {'application/json': {'example': {'status': 'ok'}}},
                        },
                        str(HTTPStatus.SERVICE_UNAVAILABLE): {
                            'description': 'Соединения с брокером нет',
                            'content': {
                                'application/json': {
                                    'example': {'status': 'unavailable', 'detail': 'event queue is not connected'},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


# Страница документации: та же Swagger UI, что показывает FastAPI в соседних
# сервисах, только подключённая руками.
SWAGGER_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <title>Сервис сбора пользовательских действий</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
    <div id="swagger"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        window.onload = () => SwaggerUIBundle({url: '/ugc/api/openapi.json', dom_id: '#swagger'});
    </script>
</body>
</html>
"""


@router.get('/openapi.json')
def openapi_json():
    """Спецификация OpenAPI."""
    return jsonify(build_spec())


@router.get('/openapi')
def openapi_page():
    """Страница документации."""
    return SWAGGER_PAGE
