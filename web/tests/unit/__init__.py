"""Unit-тесты интерфейса: страницы на подменённых API, без Docker.

Настройки читаются при импорте, поэтому обязательные переменные окружения
задаются здесь, до импорта модулей сервиса.
"""

import os

SECRET_KEY = 'unit-test-secret-key-of-at-least-32-bytes'
os.environ.setdefault('AUTH_JWT_SECRET_KEY', SECRET_KEY)
