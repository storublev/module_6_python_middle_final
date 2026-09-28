"""Распределённая трассировка: OpenTelemetry → Jaeger.

Логи каждого сервиса сами по себе не отвечают на вопрос «почему запрос шёл
две секунды»: время могло уйти в PostgreSQL, в Redis, в соседний сервис или в
сеть между ними. Трассировка собирает из всех участников дерево спанов —
каждый со своим временем, — и по нему видно, кто именно тормозил.

Спаны уходят в Jaeger по OTLP (HTTP). Прежний экспортёр `opentelemetry-exporter-jaeger`
удалён из OpenTelemetry: Jaeger с версии 1.35 принимает OTLP сам, и отдельный
агент больше не нужен.

Трассировка необязательна: без адреса коллектора сервис работает как раньше,
просто ничего не отправляет. Так его можно запустить локально, не поднимая
Jaeger.
"""

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)


def configure_tracing(app: FastAPI, service_name: str, endpoint: str, excluded_urls: str = '') -> None:
    """Включает трассировку и отправку спанов в Jaeger.

    `endpoint` пуст — трассировка выключена.

    Спаны копятся и уходят пачками (BatchSpanProcessor): отправлять каждый
    по отдельности значило бы добавлять к каждому запросу ещё один сетевой.
    Сэмплирование оставлено стандартным (все запросы): нагрузка учебная, а
    неполные данные в трассировке хуже, чем их объём.
    """
    if not endpoint:
        logger.info('Трассировка выключена: адрес коллектора не задан')
        return

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    # excluded_urls: документация и проверки живости деревьев спанов не стоят.
    FastAPIInstrumentor.instrument_app(app, excluded_urls=excluded_urls)
    logger.info('Трассировка включена, спаны уходят в %s', endpoint)
