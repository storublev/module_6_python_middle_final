"""Сборка приложения Flask.

Приложение создаётся фабрикой, а не на уровне модуля: в фабрике открывается
соединение с Kafka, и импортировать модуль (в тестах, в скриптах) значило бы
каждый раз лезть к брокеру. Gunicorn умеет вызывать фабрику сам —
`gunicorn 'main:create_app()'`.

Зелёные потоки включает воркер gunicorn: `--worker-class gevent` подменяет
сокеты стандартной библиотеки до импорта приложения, поэтому сам код про
gevent ничего не знает и в тестах работает как обычный Flask.
"""

import atexit
from logging.config import dictConfig

from flask import Flask

from api import errors, middleware, openapi
from api.dependencies import build_services, register_services
from api.v1 import events, health
from core.config import Settings, settings
from core.logger import LOGGING
from core.tracing import configure_tracing
from storage.base import EventQueue

API_PREFIX = '/ugc/api/v1'
DOCS_PREFIX = '/ugc/api'

# Эти адреса вызываются мимо шлюза — healthcheck контейнера и браузер с
# документацией, — поэтому заголовка X-Request-Id от них не требуем.
EXEMPT_PATHS = frozenset({
    f'{API_PREFIX}/health',
    f'{API_PREFIX}/ready',
    f'{DOCS_PREFIX}/openapi',
    f'{DOCS_PREFIX}/openapi.json',
})


def create_app(config: Settings | None = None, queue: EventQueue | None = None) -> Flask:
    """Собирает приложение. `queue` подменяется в тестах очередью в памяти."""
    config = config or settings
    dictConfig(LOGGING)

    app = Flask(__name__)
    # JSON отдаём как есть: кириллицу в ответах экранировать незачем.
    app.json.ensure_ascii = False
    # Трассировка подключается до маршрутов: инструментация оборачивает
    # приложение целиком.
    configure_tracing(app, config.project_name, config.otlp_endpoint, excluded_urls=','.join(EXEMPT_PATHS))

    services = build_services(config, queue)
    register_services(app, services)
    errors.register_error_handlers(app)
    middleware.register_request_id(app, config.require_request_id, EXEMPT_PATHS)

    app.register_blueprint(events.router, url_prefix=API_PREFIX)
    app.register_blueprint(health.router, url_prefix=API_PREFIX)
    app.register_blueprint(openapi.router, url_prefix=DOCS_PREFIX)

    # При остановке воркера продюсер дописывает буфер: события, которые уже
    # приняты у клиента, но ещё не ушли в брокер, иначе пропали бы.
    atexit.register(services.queue.close)
    return app
