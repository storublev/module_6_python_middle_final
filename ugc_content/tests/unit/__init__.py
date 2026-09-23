"""Unit-тесты сервиса пользовательского контента.

Настройки читаются при импорте `core.config`, а у ключа подписи токенов нет
значения по умолчанию — сервис не должен стартовать с общеизвестным ключом.
Поэтому заглушка кладётся в окружение здесь: пакет импортируется раньше
conftest и тестов.
"""

import os

SECRET_KEY = 'unit-test-secret-key-of-at-least-32-bytes'

os.environ.setdefault('AUTH_JWT_SECRET_KEY', SECRET_KEY)
