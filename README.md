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

### Локальный запуск без Docker

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd src
ELASTIC_HOST=localhost REDIS_HOST=localhost python main.py   # http://localhost:8000/api/openapi
```

## Эндпоинты

| Метод и путь | Назначение |
|---|---|
| `GET /api/v1/films?sort=-imdb_rating&genre=<uuid>` | Популярные фильмы, фильтр по жанру (он же — похожие фильмы) |
| `GET /api/v1/films/search?query=<строка>` | Поиск по названию и описанию фильмов |
| `GET /api/v1/films/<uuid>` | Полная информация по фильму |
| `GET /api/v1/genres` | Список жанров |
| `GET /api/v1/genres/<uuid>` | Данные по жанру |
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
  `uuid` и страницы списков — по хешу запроса к Elasticsearch. Время жизни —
  `CACHE_EXPIRE_IN_SECONDS` (5 минут). Если Redis недоступен, запрос идёт
  напрямую в Elasticsearch, API продолжает работать. Запись кеша, которая не
  проходит проверку модели (повреждена или осталась от прошлой версии), считается
  отсутствующей: данные читаются из Elasticsearch, и запись перезаписывается.
* Для списков из Elasticsearch запрашиваются только нужные поля (`_source`),
  подсчёт общего числа совпадений отключён.

## Структура

```
src/
├── main.py              # приложение, lifespan с клиентами ES и Redis
├── api/v1/              # роутеры, параметры запросов, схемы ответов
├── core/                # настройки, логирование, middleware
├── db/                  # клиенты Elasticsearch и Redis
├── models/              # модели документов Elasticsearch
├── services/            # бизнес-логика: базовый сервис с кешем, фильмы, жанры, персоны
└── storage/             # интерфейс хранилища документов и его реализация на Elasticsearch
```

Общая логика чтения по `id`, поиска, пагинации и кеширования — в
`services/base.py`; сервисы сущностей описывают только свои запросы.

Сервисы не зависят от Elasticsearch: запрос они описывают нейтральной
структурой `SearchRequest` (поля, пагинация, полнотекстовый поиск, связь с
жанром или персоной, сортировка) и передают хранилищу `DocumentStorage`.
Перевод в запрос Elasticsearch и обработка его сбоев — в `storage/elastic.py`.
Другое хранилище подключается новой реализацией `DocumentStorage` без правок
в сервисах.

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
