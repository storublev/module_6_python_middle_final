# Async API онлайн-кинотеатра

Асинхронный API на FastAPI — точка входа для клиентов кинотеатра. Отдаёт фильмы,
жанры и персоны из Elasticsearch и кеширует ответы в Redis. В первой итерации
все пользователи анонимные.

## Архитектура

```
клиент ──► nginx :80 ──► api (FastAPI, uvicorn) ──► redis (кеш)
                                   │
                                   └──────────────► elasticsearch ◄── etl ◄── postgres
```

API — самостоятельный сервис: он только читает индексы `movies`, `genres` и
`persons`. Их заполняет ETL, который живёт в своём репозитории
[new_admin_panel_sprint_3](https://github.com/storublev/new_admin_panel_sprint_3)
и в этот репозиторий не копируется. `docker-compose.yml` собирает ETL из соседнего
каталога (переменная `ETL_PROJECT_PATH`), чтобы все сервисы поднимались одной командой.

| Сервис | Назначение |
|---|---|
| `nginx` | Входная точка, проксирует `/api/` в API, держит пул keepalive-соединений |
| `api` | FastAPI + uvicorn (uvloop, httptools), несколько процессов-воркеров |
| `redis` | Кеш ответов, LRU-вытеснение, без персистентности |
| `elasticsearch` | Хранилище для чтения и полнотекстового поиска |
| `postgres` | Исходные данные (дамп из репозитория ETL) |
| `etl` | Перенос данных PostgreSQL → Elasticsearch с отслеживанием изменений |

## Запуск

Нужны Docker и Docker Compose v2, а рядом с этим репозиторием — клон ETL:

```bash
git clone git@github.com:storublev/new_admin_panel_sprint_3.git ../new_admin_panel_sprint_3
cp .env.example .env        # при необходимости поправить ETL_PROJECT_PATH и порты
docker compose up -d --build
```

После старта ETL загружает данные в Elasticsearch (при первом запуске — около
минуты), дальше подхватывает изменения в PostgreSQL.

* Документация OpenAPI: http://localhost/api/openapi
* Спецификация: http://localhost/api/openapi.json

В документации для клиентов описаны пагинация, ошибки (404, 422, 503 с моделью
`ErrorSchema`), назначение каждого эндпоинта и тега, поля ответов с примерами.

### Локальный запуск без Docker

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd src
ELASTIC_HOST=localhost REDIS_HOST=localhost python main.py   # http://localhost:8000/api/openapi
```

## Функциональные тесты

Тесты проверяют API снаружи, через HTTP: сами наполняют Elasticsearch, шлют
запросы и сверяют ответы. Код приложения они не импортируют, схемы индексов —
копия схем ETL (`tests/functional/testdata/es_mapping.py`). Стек: pytest,
pytest-asyncio, aiohttp.

Запуск в изолированном окружении — API, Elasticsearch, Redis и контейнер
с тестами поднимаются отдельным docker-compose, ETL и PostgreSQL не нужны:

```bash
docker compose -f tests/functional/docker-compose.yml up --build \
    --abort-on-container-exit --exit-code-from tests
docker compose -f tests/functional/docker-compose.yml down
```

Код выхода команды — результат тестов. Перед запуском контейнер тестов ждёт,
пока Elasticsearch и Redis начнут отвечать (`tests/functional/utils/wait_for_*.py`):
`depends_on` гарантирует только запуск процесса, а не готовность сервиса.

Локально, например из IDE с отладчиком: docker-compose тестов пробрасывает на
localhost порты API (8000), Elasticsearch (9200) и Redis (6379), а настройки
тестов по умолчанию смотрят туда же.

```bash
docker compose -f tests/functional/docker-compose.yml up -d --build --wait api
pip install -r tests/functional/requirements.txt
pytest tests/functional                      # или: pytest tests/functional -k search
```

Порты меняются переменными `TEST_API_PORT`, `TEST_ELASTIC_PORT`,
`TEST_REDIS_PORT`, адреса для тестов — `SERVICE_URL`, `ELASTIC_URL`,
`REDIS_HOST`, `REDIS_PORT`. **Тесты пересоздают индексы `movies`, `genres`,
`persons` и очищают Redis — не направляйте их на рабочие хранилища.**

```
tests/functional/
├── conftest.py            # фикстуры: клиенты на сессию, очистка перед тестом, запись в ES, запрос к API
├── docker-compose.yml     # API, Elasticsearch, Redis и тесты
├── Dockerfile             # образ с тестами; entrypoint.sh ждёт хранилища и запускает pytest
├── pytest.ini
├── requirements.txt
├── settings.py            # адреса API и хранилищ
├── src/                   # тесты по эндпоинтам
├── testdata/              # схемы индексов, фабрики документов, граничные значения параметров
└── utils/                 # ожидание Elasticsearch и Redis, вспомогательный код
```

Скорость: клиенты Elasticsearch, Redis и HTTP-сессия создаются один раз на
сессию, индексы — тоже; перед каждым тестом из индексов удаляются документы и
сбрасывается кеш. Документы пишутся с `refresh`, поэтому тестам не нужны паузы.

### Unit-тесты

Проверяют контракты слоя хранилищ без Docker и внешних сервисов: как адаптер
Elasticsearch строит запрос и как сервисы переживают сбой кеша.

```bash
pip install -r tests/unit/requirements.txt
pytest tests/unit
```

## Эндпоинты

| Метод и путь | Назначение |
|---|---|
| `GET /api/v1/films?sort=-imdb_rating&genre=<uuid>` | Популярные фильмы, фильтр по жанру (он же — похожие фильмы) |
| `GET /api/v1/films/search?query=<строка>` | Поиск по названию и описанию фильмов |
| `GET /api/v1/films/<uuid>` | Полная информация по фильму |
| `GET /api/v1/genres` | Список жанров |
| `GET /api/v1/genres/<uuid>` | Данные по жанру |
| `GET /api/v1/persons` | Список персон |
| `GET /api/v1/persons/search?query=<строка>` | Поиск по персонам |
| `GET /api/v1/persons/<uuid>` | Данные по персоне и её роли в фильмах |
| `GET /api/v1/persons/<uuid>/film` | Фильмы персоны |

Общие параметры списков:

* `page_number` (с 1, по умолчанию 1) и `page_size` (1–100, по умолчанию 50).
  Произведение `page_number * page_size` не больше 10 000 — дальше Elasticsearch
  не отдаёт результаты (`index.max_result_window`), на такой запрос API вернёт 422;
* `sort` — `-imdb_rating` (по умолчанию) или `imdb_rating`;
* поиск сортируется по релевантности.

Несуществующий `uuid` — 404, невалидный — 422. Если Elasticsearch недоступен
(нет соединения, истёк таймаут) или нужный индекс ещё не создан, API отвечает
503 `service temporarily unavailable`, а причину пишет в журнал.

### Решения по несостыковкам ТЗ

* **Косая черта в конце пути.** В ТЗ встречаются оба варианта (`/films/<uuid>/` и
  `/persons/<uuid>`). Маршруты объявлены без слеша, а `TrailingSlashMiddleware`
  отбрасывает слеш в конце — оба варианта работают без 307-редиректа.
* **`/persons/<uuid>/film`** — оставлено в единственном числе, как в ТЗ: на этот
  путь ориентируются клиенты.
* **Список персон `GET /api/v1/persons`** в ТЗ не описан; добавлен во втором
  спринте — функциональные тесты требуют «вывести всех людей». Сортировка по имени.
* **Поле `genre`** в ответе по фильму — тоже как в ТЗ, хотя это список.
* **`imdb_rating`** в ответах — число, а не строка `"float"` из схем ТЗ.
* **Жанр** содержит `name` (его возвращает API) и `description` (в индексе).
* Примеры из ТЗ с невалидными UUID (`g`, кириллическая `с`) и повторяющимися
  идентификаторами не годятся для фикстур.

## Производительность

* **C10k.** Нагрузка I/O-bound, поэтому весь путь запроса асинхронный:
  `AsyncElasticsearch`, `redis.asyncio`, uvicorn с uvloop. Один процесс
  обслуживает тысячи соединений без пула потоков; число процессов задаёт
  `API_WORKERS`. Перед API стоит nginx с `worker_connections 10240` и пулом
  keepalive-соединений к upstream.
* **Кеш.** Ответы кешируются в Redis по набору параметров запроса: карточки по
  `uuid` и страницы списков — по хешу запроса к хранилищу. Время жизни —
  `CACHE_EXPIRE_IN_SECONDS` (5 минут). Если Redis недоступен, запрос идёт
  напрямую в Elasticsearch, API продолжает работать. Запись кеша, которая не
  проходит проверку модели (повреждена или осталась от прошлой версии), считается
  отсутствующей: данные читаются из Elasticsearch, и запись перезаписывается.
* Для списков из Elasticsearch запрашиваются только нужные поля (`_source`),
  подсчёт общего числа совпадений отключён.

### Сбои внешних сервисов: exponential backoff

* **Elasticsearch.** Если Elasticsearch отказывает в соединении (перезапуск,
  сбой сети), `ElasticStorage` повторяет запрос с экспоненциальной паузой:
  0,1 → 0,2 → 0,4 … с, не больше 1 с, со случайным разбросом, чтобы воркеры не
  повторяли запросы одновременно. Через `ELASTIC_BACKOFF_MAX_TIME` (3 с) повторы
  прекращаются и API отвечает 503. Таймаут (`ELASTIC_REQUEST_TIMEOUT`, 2 с) не
  повторяется: зависший Elasticsearch вряд ли ответит на вторую попытку, а
  каждая заняла бы ещё столько же. Встроенные повторы клиента отключены — они
  идут без паузы и умножали бы число попыток.
* **Redis.** Кеш не должен задерживать ответ, поэтому при обрыве соединения —
  один повтор с паузой в сотые доли секунды (этого хватает, чтобы
  переподключиться после перезапуска Redis), таймауты не повторяются. Если
  Redis недоступен, запрос идёт в Elasticsearch.
* **Функциональные тесты** ждут Elasticsearch и Redis тоже с экспоненциальной
  паузой (библиотека `backoff`).

Проверено вручную на окружении тестов: при остановленном Elasticsearch API
отвечает 503 через 3 с после 12 попыток; если Elasticsearch поднимается, пока
идут повторы, запрос проходит; при остановленном Redis ответы приходят за
15–30 мс, после его перезапуска кеш снова работает.

## Структура

```
src/
├── main.py              # приложение, lifespan с клиентами ES и Redis
├── api/dependencies.py  # сборка сервисов: какие хранилища и кеш они получают
├── api/v1/              # роутеры, параметры запросов, схемы ответов
├── core/                # настройки, логирование, middleware
├── db/                  # клиенты Elasticsearch и Redis
├── models/              # модели документов Elasticsearch
├── services/            # бизнес-логика: базовый сервис с кешем, фильмы, жанры, персоны
└── storage/             # интерфейсы хранилища документов и кеша, реализации на Elasticsearch и Redis
```

Общая логика чтения по `id`, поиска, пагинации и кеширования — в
`services/base.py`; сервисы сущностей описывают только свои запросы.

Сервисы не зависят от Elasticsearch: запрос они описывают нейтральной
структурой `SearchRequest` (поля, пагинация, полнотекстовый поиск, связь с
жанром или персоной, сортировка) и передают хранилищу `DocumentStorage`.
Перевод в запрос Elasticsearch и обработка его сбоев — в `storage/elastic.py`.
Другое хранилище подключается новой реализацией `DocumentStorage` без правок
в сервисах. Синтаксис конкретного хранилища в сервисы не попадает: например,
вес поля поиска задаётся значением — `SearchField('title', weight=3)`, а в
строку `title^3` его переводит адаптер Elasticsearch.

С кешем так же: сервисы работают с `ModelCache` (`services/cache.py`), который
сериализует модели, проверяет записи и задаёт время жизни, а хранит данные
через интерфейс `Cache`. Реализация на Redis — `storage/redis.py`.

Контракт `Cache` для всех реализаций: при сбое своего хранилища методы
поднимают `CacheUnavailableError` и только его. Что делать при сбое, решает
`ModelCache` — в одном месте для любой реализации: чтение становится промахом,
запись пропускается, запрос обслуживается из Elasticsearch. Поэтому замена
Redis на другой кеш не меняет поведения API при его недоступности.

Конкретные реализации выбираются в одном месте — `api/dependencies.py`
(Composition Root): оттуда эндпоинты получают готовые сервисы через `Depends`.
Модули сервисов содержат только бизнес-логику и не импортируют ни FastAPI,
ни клиенты Elasticsearch и Redis.

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `ETL_PROJECT_PATH` | `../new_admin_panel_sprint_3` | Путь к репозиторию ETL |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `movies_database` / `app` / `123qwe` | Доступ к PostgreSQL |
| `PROJECT_NAME` | `movies` | Название в документации |
| `LOG_LEVEL` | `INFO` | Уровень логирования API |
| `CACHE_EXPIRE_IN_SECONDS` | `300` | Время жизни кеша |
| `API_WORKERS` | `2` | Количество процессов uvicorn |
| `NGINX_PORT` | `80` | Порт на хосте |
| `ES_JAVA_OPTS` | `-Xms512m -Xmx512m` | Память Elasticsearch |
| `REDIS_MAXMEMORY` | `256mb` | Лимит памяти кеша |
| `ELASTIC_REQUEST_TIMEOUT` | `2` | Сколько секунд ждать ответа Elasticsearch на одну попытку |
| `ELASTIC_BACKOFF_MAX_TIME` | `3` | Через сколько секунд прекращать повторы запроса к Elasticsearch |
| `ELASTIC_BACKOFF_FACTOR` / `ELASTIC_BACKOFF_MAX_VALUE` | `0.1` / `1` | Первая и наибольшая пауза между повторами, с |
| `REDIS_BACKOFF_RETRIES` | `1` | Повторы при обрыве соединения с Redis |
| `REDIS_BACKOFF_BASE` / `REDIS_BACKOFF_CAP` | `0.01` / `0.1` | Первая и наибольшая пауза между повторами к Redis, с |
