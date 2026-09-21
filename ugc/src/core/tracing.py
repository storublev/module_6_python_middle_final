"""Распределённая трассировка: OpenTelemetry → Jaeger.

То же, что в Async API и сервисе авторизации, но для WSGI-приложения: спаны
уходят в Jaeger по OTLP, а в каждый спан кладётся `X-Request-Id`, чтобы запрос
находился в трассировке по той же строке, что записана в журнале nginx.

Трассировка необязательна: без адреса коллектора сервис работает как обычно и
ничего не отправляет — так его можно запустить локально, не поднимая Jaeger.
"""

import logging

from flask import Flask
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)


def configure_tracing(app: Flask, service_name: str, endpoint: str, excluded_urls: str = '') -> None:
    """Включает трассировку и отправку спанов в Jaeger.

    `endpoint` пуст — трассировка выключена.

    Спаны копятся и уходят пачками (BatchSpanProcessor): отправлять каждый по
    отдельности значило бы добавлять к каждому запросу ещё один сетевой — а у
    этого сервиса запросов больше всех.
    """
    if not endpoint:
        logger.info('Трассировка выключена: адрес коллектора не задан')
        return

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    # excluded_urls: документация и проверки живости деревьев спанов не стоят.
    FlaskInstrumentor().instrument_app(app, excluded_urls=excluded_urls)
    logger.info('Трассировка включена, спаны уходят в %s', endpoint)
