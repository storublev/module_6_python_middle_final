"""Unit-тесты сервиса бронирования: бизнес-логика на хранилищах в памяти, без Docker.

Настройки сервиса читаются при импорте, поэтому обязательные переменные
окружения задаются здесь, до импорта модулей сервиса.
"""

import os

SECRET_KEY = 'unit-test-secret-key-of-at-least-32-bytes'
os.environ.setdefault('AUTH_JWT_SECRET_KEY', SECRET_KEY)
os.environ.setdefault('AUTH_SERVICE_TOKEN', 'unit-test-service-token')
os.environ.setdefault('BOOKING_POSTGRES_PASSWORD', 'unit-tests')
