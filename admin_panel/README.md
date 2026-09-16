# Админка каталога

Django-админка онлайн-кинотеатра: через неё редакторы ведут фильмы, жанры и
персоны. Она работает с той же базой, из которой ETL наполняет Elasticsearch,
поэтому сохранённая правка доезжает до выдачи Async API без ручных действий.

Вход сотрудников проверяет [сервис авторизации](../auth/README.md): отдельного
пароля в админке у сотрудника нет.

## Как это устроено

```
сотрудник ──► nginx ──► django-admin ──► postgres (схема content — каталог,
                             │            public — сотрудники и сессии)
                             └─────────► auth (вход и право admin.access)

правка ──► postgres.content ──► etl ──► elasticsearch ──► api
```

| Каталог | Что внутри |
|---|---|
| `config/` | Настройки (pydantic-settings), маршруты, WSGI |
| `movies/` | Модели каталога и их страницы в админке |
| `users/` | Сотрудник, бэкенд аутентификации, клиент сервиса авторизации, прерыватель |
| `tests/unit/` | Модульные тесты: бэкенд, клиент, прерыватель |

### Схема базы: два владельца, одна база

Таблицы каталога (`content.film_work`, `content.genre`, `content.person` и
связи) создаёт дамп из репозитория ETL, а не Django. Поэтому модели `movies`
объявлены `managed = False`, миграций у приложения нет, и `migrate` их не
трогает: у схемы один владелец. Нужную схему подставляет путь поиска
`search_path=public,content`.

Собственные таблицы Django — сотрудники (`public.admin_user`), сессии, журнал
действий, типы содержимого — заводит миграция в схеме `public`.

### Вход через сервис авторизации

`users.backends.AuthServiceBackend` стоит первым в `AUTHENTICATION_BACKENDS` и
на каждый вход делает четыре шага:

1. `POST /auth/api/v1/login` — обменивает логин и пароль на access-токен;
2. `GET /auth/api/v1/users/me` — кто это: идентификатор, логин, признак суперпользователя;
3. `GET /auth/api/v1/access/check?permission=admin.access&fresh=true` — можно ли
   ему в админку (`fresh=true` — по базе, минуя кеш прав: отозванный доступ
   закрывается сразу);
4. `POST /auth/api/v1/logout` — закрывает сессию в сервисе: дальше сотрудника
   пускает сессия Django, а токены хранить негде.

Локальная запись сотрудника заводится с идентификатором из сервиса авторизации
и без пригодного пароля. Право проверяется при входе, а не при каждом клике,
поэтому доступ живёт не дольше сессии Django; закрыть его немедленно можно,
сняв галочку «активен» у сотрудника в админке.

Право `admin.access` даёт роль `staff` (миграция `0005` сервиса авторизации).
Назначить её сотруднику:

```bash
docker compose exec auth python cli.py createsuperuser --login admin   # если суперпользователя ещё нет
TOKEN=$(curl -s -X POST http://localhost/auth/api/v1/login \
    -H 'Content-Type: application/json' -d '{"login":"admin","password":"..."}' \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
ROLE=$(curl -s http://localhost/auth/api/v1/roles -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import sys,json; print([r["id"] for r in json.load(sys.stdin) if r["name"]=="staff"][0])')
curl -X PUT "http://localhost/auth/api/v1/users/<id сотрудника>/roles/$ROLE" \
    -H "Authorization: Bearer $TOKEN"
```

### Если сервис авторизации недоступен

Сервис авторизации — внешняя зависимость, и админка не должна вставать вместе
с ним:

* у каждого запроса явные таймауты (`DJANGO_AUTH_CONNECT_TIMEOUT`,
  `DJANGO_AUTH_READ_TIMEOUT`) — форма входа не висит;
* повторяется только обрыв соединения, с растущей паузой и случайной добавкой;
  таймаут ответа не повторяется: сервис мог уже обработать запрос, и повтор
  съел бы лимит попыток входа;
* пять сбоев подряд размыкают прерыватель (`users/circuit_breaker.py`) на 30
  секунд: запросы сразу получают отказ и не держат воркеры. Через паузу
  проходит один пробный запрос, успех закрывает прерыватель;
* вход отклоняется с обычной ошибкой формы, а не пятисоткой; уже вошедшие
  сотрудники продолжают работать — их пускает сессия Django.

На случай долгого простоя сервиса авторизации в `AUTHENTICATION_BACKENDS`
вторым оставлен `ModelBackend`: локальный суперпользователь, созданный
`manage.py createsuperuser`, войдёт по локальному паролю. Его логин не должен
совпадать с логином из сервиса авторизации — иначе записи столкнутся на
уникальном логине, и вход через сервис будет отклонён.

## Запуск

Админка поднимается вместе со стеком из корня репозитория (см.
[README проекта](../README.md#запуск)) и доступна на http://localhost/admin/.
Миграции и сборку статики выполняет одноразовый контейнер
`django-admin-migrations`, статику раздаёт nginx из тома `admin_static`.

Аварийный локальный суперпользователь:

```bash
docker compose exec django-admin python manage.py createsuperuser --login rescue
```

### Локальный запуск без Docker

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DJANGO_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
export DJANGO_POSTGRES_PASSWORD=123qwe DJANGO_AUTH_API_URL=http://localhost/auth
python manage.py migrate
python manage.py runserver   # http://localhost:8000/admin/
```

## Переменные окружения

Все — с префиксом `DJANGO_`: файл `.env` общий для сервисов кинотеатра.
Обязателен только `DJANGO_SECRET_KEY` (не короче 32 символов) и
`DJANGO_POSTGRES_PASSWORD` — без них админка не стартует, а не работает с
общеизвестным ключом.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DJANGO_SECRET_KEY` | — | Ключ подписи сессий и CSRF-токенов |
| `DJANGO_DEBUG` | `False` | Отладочный режим |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Домены, с которых принимаются запросы |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `http://localhost,http://127.0.0.1` | Источники, которым доверяет проверка CSRF |
| `DJANGO_LOG_LEVEL` | `INFO` | Уровень журнала |
| `DJANGO_POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `127.0.0.1` / `5432` / `movies_database` / `app` / — | База фильмов, общая с ETL |
| `DJANGO_POSTGRES_SCHEMA` | `content` | Схема каталога в пути поиска |
| `DJANGO_AUTH_API_URL` | `http://auth:8000` | Адрес сервиса авторизации |
| `DJANGO_AUTH_ADMIN_PERMISSION` | `admin.access` | Право, дающее вход в админку |
| `DJANGO_AUTH_CONNECT_TIMEOUT` | `1.0` | Таймаут соединения с сервисом авторизации, с |
| `DJANGO_AUTH_READ_TIMEOUT` | `3.0` | Таймаут ответа, с |
| `DJANGO_AUTH_CONNECT_RETRIES` | `2` | Повторов при обрыве соединения |
| `DJANGO_AUTH_BACKOFF_FACTOR` | `0.2` | Основание растущей паузы между повторами |
| `DJANGO_AUTH_BREAKER_FAILURES` | `5` | Сбоев подряд до размыкания прерывателя |
| `DJANGO_AUTH_BREAKER_RESET_TIMEOUT` | `30.0` | Пауза прерывателя, с |
| `DJANGO_STATIC_ROOT` | `admin_panel/staticfiles` | Куда собирается статика |
| `DJANGO_STATIC_URL` | `/static/` | Префикс статики |

## Тесты

Модульные тесты идут без Docker и без PostgreSQL: таблицу сотрудников создаёт
миграция в SQLite, сеть и сервис авторизации заменены заглушками
(`tests/unit/fakes.py`). Проверяются разбор ответов сервиса и таймауты,
переходы прерывателя и решения бэкенда: кого пускать, что писать в базу,
закрывается ли сессия в сервисе авторизации.

```bash
pip install -r tests/unit/requirements.txt
cd tests/unit && pytest
```

Линтеры:

```bash
ruff check . && flake8 .
```
