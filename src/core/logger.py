from core.config import settings

# Идентификатор запроса в каждой записи: по нему собирается история
# запроса по всем сервисам сразу (см. core/request_id.py).
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s'
LOG_DEFAULT_HANDLERS = ['console']

# Конфигурация логирования для uvicorn и приложения.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {'()': 'core.request_id.RequestIdFilter'},
    },
    'formatters': {
        'verbose': {'format': LOG_FORMAT},
        'default': {
            '()': 'uvicorn.logging.DefaultFormatter',
            'fmt': '%(levelprefix)s %(message)s',
            'use_colors': None,
        },
        'access': {
            '()': 'uvicorn.logging.AccessFormatter',
            'fmt': "%(levelprefix)s %(client_addr)s - '%(request_line)s' %(status_code)s",
        },
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'filters': ['request_id'],
        },
        'default': {
            'formatter': 'default',
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
        },
        'access': {
            'formatter': 'access',
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
        },
    },
    'loggers': {
        '': {
            'handlers': LOG_DEFAULT_HANDLERS,
            'level': settings.log_level,
        },
        'uvicorn.error': {
            'level': settings.log_level,
        },
        'uvicorn.access': {
            'handlers': ['access'],
            'level': settings.log_level,
            'propagate': False,
        },
    },
    'root': {
        'level': settings.log_level,
        'formatter': 'verbose',
        'handlers': LOG_DEFAULT_HANDLERS,
    },
}
