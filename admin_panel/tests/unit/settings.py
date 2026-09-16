"""Настройки Django для модульных тестов: без PostgreSQL и без секретов из окружения.

Таблицу сотрудников создаёт миграция, поэтому она появится и в SQLite.
Модели каталога объявлены managed = False — их таблиц в тестовой базе нет, и
модульные тесты в них не ходят: каталог проверяется на живом стеке.
"""

import os

os.environ.setdefault('DJANGO_SECRET_KEY', 'unit-test-secret-key-of-at-least-32-bytes')
os.environ.setdefault('DJANGO_POSTGRES_PASSWORD', 'unit-test')

from config.settings import *  # noqa: E402,F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}
