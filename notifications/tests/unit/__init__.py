"""Окружение unit-тестов.

Настройки сервиса читаются при импорте его модулей, а у секретов нет значений
по умолчанию — поэтому заглушки задаются здесь, до первого импорта.
"""

import os

SECRET_KEY = 'unit-test-secret-key-of-at-least-32-bytes'
os.environ.setdefault('AUTH_JWT_SECRET_KEY', SECRET_KEY)
os.environ.setdefault('AUTH_SERVICE_TOKEN', 'unit-test-service-token')
os.environ.setdefault('NOTIFY_POSTGRES_PASSWORD', 'unit-tests')
os.environ.setdefault('NOTIFY_RABBIT_URL', 'amqp://guest:guest@localhost:5672/')
