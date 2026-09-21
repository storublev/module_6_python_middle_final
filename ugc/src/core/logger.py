"""Настройка журнала: тот же формат, что у остальных сервисов кинотеатра."""

from core.config import settings

# Идентификатор запроса в каждой записи: по нему собирается история запроса по
# всем сервисам сразу (см. core/request_id.py).
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {'()': 'core.request_id.RequestIdFilter'},
    },
    'formatters': {
        'verbose': {'format': LOG_FORMAT},
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
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
