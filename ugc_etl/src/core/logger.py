"""Настройка журнала: тот же формат, что у остальных сервисов кинотеатра.

Идентификатора запроса здесь нет: ETL работает не по запросу пользователя, а
сам по себе, и связывать его записи не с чем.
"""

from core.config import settings

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {'format': LOG_FORMAT},
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        # kafka-python подробно рассказывает о метаданных и перераспределении
        # партиций на уровне INFO — интересны только предупреждения.
        'kafka': {'level': 'WARNING'},
    },
    'root': {
        'level': settings.log_level,
        'handlers': ['console'],
    },
}
