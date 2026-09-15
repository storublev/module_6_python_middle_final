# Сервис авторизации

Регистрация, вход и управление доступом пользователей онлайн-кинотеатра:
пара JWT-токенов (access + refresh), сессии с мгновенным выходом, история
входов, роли с наборами прав и быстрая проверка прав с кешем.

* Архитектура, модель доступа, схема данных, обработка ошибок —
  [docs/architecture.md](docs/architecture.md).
* Спецификация API — [docs/openapi.json](docs/openapi.json), на работающем
  сервисе — http://localhost/auth/api/openapi.

Стек: Python 3.12, FastAPI, SQLAlchemy 2 (asyncpg), Alembic, PostgreSQL 16,
Redis 7, PyJWT, pwdlib (Argon2), Typer.

## Запуск с нуля

Сервис поднимается вместе с остальными сервисами кинотеатра из корня
репозитория (подробнее — в [README проекта](../README.md#запуск)):

```bash
cp .env.example .env
# задать в .env AUTH_POSTGRES_PASSWORD и AUTH_JWT_SECRET_KEY, ключ — не короче 32 символов:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose up -d --build
```

Без `AUTH_POSTGRES_PASSWORD` и `AUTH_JWT_SECRET_KEY` docker-compose не
запустится: у секретов нет значений по умолчанию.

Контейнер `auth-migrations` применяет миграции и завершается, после него
стартует `auth`. Первого суперпользователя создаёт консольная команда:

```bash
docker compose exec auth python cli.py createsuperuser --login admin
# пароль спрашивается дважды без отображения; без терминала — переменной окружения:
docker compose exec -e AUTH_SUPERUSER_PASSWORD=... auth python cli.py createsuperuser --login admin
```

Суперпользователю разрешено всё, в том числе управлять ролями. Проверка:

```bash
curl -s -X POST localhost/auth/api/v1/signup -H 'Content-Type: application/json' \
    -d '{"login": "neo", "password": "followtherabbit"}'
curl -s -X POST localhost/auth/api/v1/login -H 'Content-Type: application/json' \
    -d '{"login": "neo", "password": "followtherabbit"}'
curl -s localhost/auth/api/v1/users/me -H 'Authorization: Bearer <access_token>'
```

### Полезные команды

| Команда | Что делает |
|---|---|
| `docker compose logs -f auth` | журнал сервиса |
| `docker compose run --rm auth-migrations` | применить новые миграции |
| `docker compose exec auth alembic current` | текущая версия схемы |
| `docker compose exec auth python cli.py --help` | консольные команды |
| `docker compose exec auth-postgres psql -U auth -d auth` | консоль базы |
| `docker compose exec auth-redis redis-cli --scan --pattern 'auth:*'` | ключи сессий и кеша |

### Локально, без Docker

Нужны PostgreSQL и Redis; адреса — переменными `AUTH_*` (см. ниже).

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r auth/requirements.txt
cd auth
export AUTH_POSTGRES_PASSWORD=... AUTH_JWT_SECRET_KEY=...
alembic upgrade head
cd src && python main.py            # http://127.0.0.1:8000/auth/api/openapi
```

## Тесты

**Unit-тесты** — бизнес-логика на хранилищах в памяти, адаптеры Redis на
fakeredis, консольная команда, актуальность `docs/openapi.json`. Docker не нужен:

```bash
pip install -r auth/tests/unit/requirements.txt
pytest auth/tests/unit
```

**Функциональные тесты** — сервис в Docker со своими PostgreSQL и Redis,
тесты ходят к нему по HTTP. Проверяется каждый ответ каждого эндпоинта:
успех, все коды 401 (без токена, истёкший, поддельный, повреждённый, из
закрытой сессии), 403, 404, 409, 422. Прогон — около 30 секунд:

```bash
docker compose -f auth/tests/functional/docker-compose.yml up --build \
    --abort-on-container-exit --exit-code-from tests
docker compose -f auth/tests/functional/docker-compose.yml down
```

Код выхода — результат тестов. Порты сервиса (8001), PostgreSQL (5433) и
Redis (6380) проброшены на localhost, так что тесты можно запускать и из IDE:

```bash
docker compose -f auth/tests/functional/docker-compose.yml up -d --build --wait auth
pip install -r auth/tests/functional/requirements.txt
pytest auth/tests/functional
```

Порты меняются переменными `TEST_AUTH_PORT`, `TEST_POSTGRES_PORT`,
`TEST_REDIS_PORT`. **Тесты удаляют всех пользователей и очищают Redis — не
направляйте их на рабочие хранилища.**

**Линтеры** — настройки в `setup.cfg` и `pyproject.toml`:

```bash
cd auth && flake8 . && ruff check .
```

После изменения API обновите спецификацию: `cd auth/src && python export_openapi.py`.

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `AUTH_JWT_SECRET_KEY` | — | Ключ подписи JWT, не короче 32 символов. Обязателен |
| `AUTH_POSTGRES_PASSWORD` | — | Пароль PostgreSQL сервиса. Обязателен |
| `AUTH_POSTGRES_HOST` / `AUTH_POSTGRES_PORT` | `127.0.0.1` / `5432` | Адрес PostgreSQL (в compose — `auth-postgres`) |
| `AUTH_POSTGRES_DB` / `AUTH_POSTGRES_USER` | `auth` / `auth` | База и пользователь |
| `AUTH_REDIS_HOST` / `AUTH_REDIS_PORT` / `AUTH_REDIS_DB` | `127.0.0.1` / `6379` / `0` | Адрес Redis (в compose — `auth-redis`) |
| `AUTH_ACCESS_TOKEN_TTL` | `900` | Время жизни access-токена, секунды или ISO 8601 (`PT15M`) |
| `AUTH_REFRESH_TOKEN_TTL` | `1209600` | Время жизни refresh-токена и сессии, 14 дней |
| `AUTH_ACCESS_CACHE_TTL` | `600` | Время жизни кеша прав |
| `AUTH_JWT_ALGORITHM` | `HS256` | Алгоритм подписи JWT |
| `AUTH_REDIS_BACKOFF_RETRIES` | `2` | Повторы при обрыве соединения с Redis |
| `AUTH_REDIS_BACKOFF_BASE` / `AUTH_REDIS_BACKOFF_CAP` | `0.05` / `0.5` | Первая и наибольшая пауза между повторами, с |
| `AUTH_LOG_LEVEL` | `INFO` | Уровень логирования |
| `AUTH_POSTGRES_ECHO` | `false` | Писать SQL-запросы в журнал |
| `AUTH_WORKERS` | `2` | Процессов uvicorn в контейнере (переменная docker-compose) |
| `AUTH_SUPERUSER_PASSWORD` | — | Пароль для `createsuperuser` без терминала |

Префикс `AUTH_` у всех переменных: сервис делит `.env` с остальными сервисами
кинотеатра, и без префикса его `POSTGRES_DB` совпал бы с базой фильмов.
