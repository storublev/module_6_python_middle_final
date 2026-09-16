# Сервис авторизации

Регистрация, вход и управление доступом пользователей онлайн-кинотеатра:
пара JWT-токенов (access + refresh), сессии с мгновенным выходом, история
входов, роли с наборами прав и быстрая проверка прав с кешем. Войти можно и
через соцсеть — Яндекс или Google (раздел [«Вход через соцсети»](#вход-через-соцсети)).

* Архитектура, модель доступа, схема данных, обработка ошибок —
  [docs/architecture.md](docs/architecture.md).
* Спецификация API — [docs/openapi.json](docs/openapi.json), на работающем
  сервисе — http://localhost/auth/api/openapi.

Стек: Python 3.12, FastAPI, SQLAlchemy 2 (asyncpg), Alembic, PostgreSQL 16,
Redis 7, PyJWT, pwdlib (Argon2), Authlib, Typer.

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
fakeredis, консольная команда, актуальность `docs/openapi.json`. Обмен с
поставщиком OAuth проверяется с подменённым транспортом: настоящие Яндекс и
Google в тестах не нужны. Docker не требуется:

```bash
pip install -r auth/tests/unit/requirements.txt
pytest auth/tests/unit
```

**Функциональные тесты** — сервис в Docker со своими PostgreSQL и Redis,
тесты ходят к нему по HTTP. Проверяется каждый ответ каждого эндпоинта:
успех, все коды 401 (без токена, истёкший, поддельный, повреждённый, из
закрытой сессии), 403, 404, 409, 422, 429. Настоящего поставщика OAuth в
окружении нет, поэтому у входа через соцсеть проверяется всё, что видно
снаружи: список поставщиков, ссылка авторизации со `state`, отказы на неверный
возврат и личный кабинет. Прогон — около 30 секунд:

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

## Вход через соцсети

Сервис выступает **потребителем** OAuth 2.0: своего сервера OAuth здесь нет,
фронтенда — тоже. Реализован Authorization Code Flow.

```
пользователь ──► GET /oauth/yandex/login ──► 307 на oauth.yandex.ru?...&state=…
                                                        │
                        пользователь входит и соглашается│
                                                        ▼
пользователь ◄── пара токенов ◄── GET /oauth/yandex/callback?code=…&state=…
                                       │
                                       └─► обмен кода на токен + данные пользователя
                                           (с client_secret, с бэкенда)
```

| Метод и путь | Назначение |
|---|---|
| `GET /oauth/providers` | Соцсети, через которые можно войти |
| `GET /oauth/{provider}/login` | Переход к поставщику. С access-токеном — привязка аккаунта, без него — вход |
| `GET /oauth/{provider}/callback` | Возврат от поставщика: выдаёт пару токенов или привязывает аккаунт |
| `GET /users/me/social-accounts` | Связанные аккаунты в личном кабинете |
| `DELETE /users/me/social-accounts/{provider}` | Открепить аккаунт |

**Код, а не токен.** Поставщик возвращает одноразовый код, а меняет его на
токен бэкенд, где лежит `client_secret`. Перехваченный код без секрета
бесполезен — этим Authorization Code Flow отличается от Implicit, отдающего
токен прямо в адресной строке.

**state против CSRF.** Переход к поставщику и возврат от него связывает
`state` — случайная строка в `auth:oauth_state:<state>`. Без неё чужой ответ
поставщика привязал бы к жертве чужой аккаунт. state одноразовый (читается
через `GETDEL`), живёт `AUTH_OAUTH_STATE_TTL` и помнит, чей это был вход:
к кому привязывать аккаунт, решается **в начале**, а не на возврате.

**Опознание по social_id.** Пользователь ищется только по паре
(`provider`, `social_id`). Искать по email нельзя: email в соцсети меняют и не
всегда подтверждают, и тогда чужой аккаунт открыл бы доступ к чужой учётной
записи.

**Учётная запись без пароля.** Первый вход через соцсеть заводит пользователя
с `password_hash = NULL`: войти в него по паролю нельзя, и время ответа этого
не выдаёт — пароль сверяется с заглушкой, как для несуществующего логина.
Логин собирается из имени поставщика и его идентификатора; владелец сменит его
в личном кабинете. Первый пароль задаётся через `PUT /users/me/password` без
поля `password`: подтверждать нечем, личность подтверждает сессия.

**Открепление.** Последний способ войти открепить нельзя (409
`last_login_method`): пользователь без пароля и без связанных аккаунтов
потерял бы доступ. Сначала задайте пароль или привяжите другую соцсеть.

### Как включить поставщика

Поставщик работает, только когда заданы оба его ключа; без них его эндпоинты
отвечают 404 `provider_not_found`, а остальной вход работает как прежде.

1. Зарегистрируйте приложение: [Яндекс](https://oauth.yandex.ru/client/new)
   (права «Доступ к адресу электронной почты» и «Доступ к логину, имени и
   фамилии, полу»), [Google](https://console.cloud.google.com/apis/credentials)
   (OAuth client ID, тип Web application).
2. Укажите там адрес возврата — посимвольно тот же, что соберёт сервис:
   `http://localhost/auth/api/v1/oauth/yandex/callback`.
3. Положите ключи в `.env` проекта:

```bash
AUTH_OAUTH_YANDEX_CLIENT_ID=...
AUTH_OAUTH_YANDEX_CLIENT_SECRET=...
AUTH_OAUTH_REDIRECT_BASE_URL=http://localhost
```

Проверить: http://localhost/auth/api/v1/oauth/providers, затем открыть в
браузере http://localhost/auth/api/v1/oauth/yandex/login.

Добавить VK или другую соцсеть — значит дописать описание в
`src/storage/oauth.py` (адреса, права, разбор ответа) и пару переменных:
бизнес-логика об отдельных поставщиках не знает.

## Трассировка и идентификатор запроса

Запрос к кинотеатру проходит через nginx, сервис контента, этот сервис и
админку. Логи каждого сами по себе не отвечают, почему запрос шёл две секунды:
время могло уйти в PostgreSQL, в Redis, в соседний сервис или в сеть.

**Идентификатор запроса.** nginx выдаёт его каждому входящему запросу
(`$request_id`) и передаёт заголовком `X-Request-Id`, перезаписывая
присланный клиентом: иначе клиент слил бы свои запросы с чужими в журналах.
Дальше он:

* пишется в access-лог nginx (`rid=…`) и в каждую запись журнала сервиса;
* становится тегом спана `http.request_id` — по нему запрос ищется в Jaeger;
* возвращается клиенту тем же заголовком: его можно назвать в обращении в поддержку;
* уходит дальше, в исходящие вызовы (сервис контента → проверка права,
  админка → вход сотрудника).

Запрос **без заголовка отклоняется** с 400 `request_id_required`: он пришёл
мимо шлюза и не найдётся ни в журналах, ни в Jaeger. Исключение — документация
(`/auth/api/openapi`, `/auth/api/openapi.json`): её открывают браузером, и её
же дёргает проверка живости контейнера. Проверку можно выключить
`AUTH_REQUIRE_REQUEST_ID=false` — сервис, запущенный локально без nginx, иначе
не отладить.

**Спаны** уходят в Jaeger по OTLP (`AUTH_OTLP_ENDPOINT`). Прежний
`opentelemetry-exporter-jaeger` удалён из OpenTelemetry: Jaeger с версии 1.35
принимает OTLP сам, и отдельный агент не нужен. Без адреса коллектора
трассировка выключается, и сервис работает как раньше.

Посмотреть: http://localhost:16686, сервис `auth`, поиск по тегу
`http.request_id=<идентификатор из ответа или из журнала nginx>`.

## История входов: месячные секции

По заданию история растёт до миллионов записей, поэтому таблица разбита на
секции по месяцам (RANGE по `created_at`, миграция `0007`). Что это даёт:

* **чистку отсечением** — год истории удаляется `DROP TABLE` одной секции, а
  не построчным `DELETE` по миллионам строк;
* **горячие данные отдельно от холодных** — запросы идут за последними
  входами, то есть в одну-две свежие секции;
* **обслуживание по частям** — VACUUM, REINDEX и резервное копирование
  работают с секцией, а не с таблицей целиком.

Почему не по типу устройства, как в примере урока: таких секций было бы
три-четыре, они не уменьшаются со временем и делят таблицу поперёк её роста.
Почему не по `user_id` (HASH): каждый запрос попадал бы ровно в одну секцию,
но старые записи не удалить отсечением — а это здесь главное.

Чтение истории одного пользователя фильтра по дате не содержит, поэтому секции
по условию не отсекаются. Зато в каждой есть индекс `(user_id, created_at)`, а
выдача постраничная: PostgreSQL сливает отсортированные потоки из секций
(MergeAppend) и останавливается, набрав страницу.

Секции будущих месяцев создаются заранее:

```bash
docker compose exec auth python cli.py create-login-partitions --months 6
```

Команду вызывают по расписанию. Пропущенный месяц не теряется: записи попадут
в секцию по умолчанию. В большом проекте эту работу берёт на себя pg_partman.

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
| `AUTH_MAX_SESSIONS_PER_USER` | `20` | Сессий (устройств) у пользователя одновременно; вход сверх предела закрывает самые давно не продлевавшиеся |
| `AUTH_ACCESS_CACHE_TTL` | `600` | Время жизни кеша прав |
| `AUTH_ACCESS_INVALIDATION_INTERVAL` / `…_MAX_INTERVAL` | `5` / `60` | Как часто повторять сброс кеша прав, отложенный из-за сбоя Redis; наибольшая пауза при сбоях |
| `AUTH_LOGIN_ATTEMPTS_PER_IP` / `…_PERIOD` | `20` / `60` | Попыток входа с одного IP за скользящее окно, секунды |
| `AUTH_LOGIN_ATTEMPTS_PER_ACCOUNT` / `…_PERIOD` | `10` / `900` | Неудачных попыток входа в один логин за окно |
| `AUTH_SIGNUP_ATTEMPTS_PER_IP` / `…_PERIOD` | `10` / `3600` | Регистраций с одного IP за окно |
| `AUTH_JWT_ALGORITHM` | `HS256` | Алгоритм подписи JWT |
| `AUTH_REDIS_BACKOFF_RETRIES` | `2` | Повторы при обрыве соединения с Redis |
| `AUTH_REDIS_BACKOFF_BASE` / `AUTH_REDIS_BACKOFF_CAP` | `0.05` / `0.5` | Первая и наибольшая пауза между повторами, с |
| `AUTH_LOG_LEVEL` | `INFO` | Уровень логирования |
| `AUTH_POSTGRES_ECHO` | `false` | Писать SQL-запросы в журнал |
| `AUTH_WORKERS` | `2` | Процессов uvicorn в контейнере (переменная docker-compose) |
| `AUTH_SUPERUSER_PASSWORD` | — | Пароль для `createsuperuser` без терминала |
| `AUTH_OAUTH_YANDEX_CLIENT_ID` / `…_SECRET` | — | Ключи приложения Яндекса; без них вход через него выключен |
| `AUTH_OAUTH_GOOGLE_CLIENT_ID` / `…_SECRET` | — | Ключи приложения Google |
| `AUTH_OAUTH_REDIRECT_BASE_URL` | `http://localhost` | Основа адреса возврата; должна совпадать с указанной у поставщика |
| `AUTH_OAUTH_STATE_TTL` | `600` | Сколько живёт начатый вход через соцсеть |
| `AUTH_OAUTH_REQUEST_TIMEOUT` | `5` | Таймаут запросов к поставщику, с |
| `AUTH_OTLP_ENDPOINT` | — | Куда отправлять спаны; пусто — трассировка выключена |
| `AUTH_REQUIRE_REQUEST_ID` | `true` | Отклонять запросы без `X-Request-Id` |

Префикс `AUTH_` у всех переменных: сервис делит `.env` с остальными сервисами
кинотеатра, и без префикса его `POSTGRES_DB` совпал бы с базой фильмов.
