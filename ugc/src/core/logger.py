"""Настройка журнала: одна запись — один JSON, как у остальных сервисов кинотеатра.

Почему JSON, а не строка. Логи всех сервисов собираются в общий ELK
(deploy/observability/), а там запись разбирается на поля: искать по
`request_id` или `level` можно, только если это поля, а не кусок текста. Разбор
строк grok-выражениями на стороне Logstash — лишняя работа и лишний источник
ошибок: формат меняется в сервисе, а ломается в конвейере.

Сервис пишет в stdout, а доставкой занимается сборщик рядом (filebeat или
gelf-драйвер Docker): приложение не должно знать, куда уезжают его логи.
"""

import json
import logging
from datetime import UTC, datetime

from core.config import settings

# Поля LogRecord, которые уже разложены по колонкам записи или не нужны в ней:
# всё остальное, что положил вызывающий (`logger.info(..., extra={...})`),
# попадает в JSON как дополнительные поля.
_STANDARD_FIELDS = frozenset({
    'args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename', 'funcName',
    'levelname', 'levelno', 'lineno', 'module', 'msecs', 'message', 'msg', 'name',
    'pathname', 'process', 'processName', 'relativeCreated', 'stack_info',
    'taskName', 'thread', 'threadName', 'request_id',
})


class JsonFormatter(logging.Formatter):
    """Превращает запись журнала в одну строку JSON."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            '@timestamp': datetime.fromtimestamp(record.created, UTC).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'service': self._service,
            'message': record.getMessage(),
            'request_id': getattr(record, 'request_id', '-'),
        }
        if record.exc_info:
            # Трейсбек — одним полем, а не отдельными строками: иначе в ELK
            # многострочное исключение превращается в десяток записей, между
            # которыми уже не видно связи.
            payload['exception'] = self.formatException(record.exc_info)
        payload.update({
            key: value for key, value in record.__dict__.items()
            if key not in _STANDARD_FIELDS and not key.startswith('_')
        })
        return json.dumps(payload, ensure_ascii=False, default=str)


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {'()': 'core.request_id.RequestIdFilter'},
    },
    'formatters': {
        'json': {'()': 'core.logger.JsonFormatter', 'service': settings.project_name},
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'json',
            'filters': ['request_id'],
        },
    },
    'loggers': {
        # kafka-python подробно рассказывает о метаданных и соединениях на
        # уровне INFO — для нас это шум, интересны только предупреждения.
        'kafka': {'level': 'WARNING'},
    },
    'root': {
        'level': settings.log_level,
        'handlers': ['console'],
    },
}
