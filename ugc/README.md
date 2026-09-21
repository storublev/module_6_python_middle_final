# Сервис сбора пользовательских действий

Принимает события онлайн-кинотеатра — клики, просмотры страниц и события
плеера — и складывает их в Kafka, откуда [ETL](../ugc_etl/README.md) переносит
их в ClickHouse. Свой сбор вместо Яндекс Метрики нужен потому, что к событию
можно присоединить собственные данные о пользователе, а внешние счётчики на
такой нагрузке упираются в свои ограничения.

Требования, из которых сервис вырос, — [docs/requirements.md](../docs/requirements.md);
решения и схемы — [docs/architecture/](../docs/architecture/README.md).

## Как он устроен

```
клиент ──(пачка событий, Bearer access-токен)──► nginx /ugc/ ──► ugc ──► Kafka ugc.events
```

* **Flask с воркерами gevent** (gunicorn). Почему не FastAPI, как остальные
  сервисы, — ADR-3: курс предлагает попробовать другой стек, а задача сервиса
  проста — принять JSON и записать его в брокер. Зелёные потоки переключаются
  на ожидании брокера сами, поэтому один процесс держит тысячу соединений.
* **Токен проверяется на месте**, без обращения к сервису авторизации
  (ADR-4): подпись HS256 тем же секретом, которым токен выпущен, плюс срок
  действия и тип. При пиковых 420 запросах в секунду сетевой вызов на каждый
  запрос сделал бы сервис авторизации узким местом.
* **События едут пачками** (ФТ-4): клиент копит их и отправляет одним
  запросом. Каждое событие проверяется отдельно, испорченное не отменяет
  остальные.
* **Ключ партиционирования — сессия просмотра**: события одной сессии
  попадают в одну партицию и приходят потребителю по порядку, а разные сессии
  расходятся по партициям равномерно.
* **Ответ 202, а не 200**: события записаны в брокер, но ещё не доехали до
  аналитического хранилища — обещать это в ответе было бы неправдой.

Слои те же, что у остальных сервисов проекта:

| Каталог | Что внутри |
|---|---|
| `src/api` | HTTP: маршруты, формат ошибок, проверка токена, идентификатор запроса, OpenAPI |
| `src/core` | Настройки, журнал, трассировка |
| `src/models` | Контракт событий на pydantic |
| `src/services` | Приём пачки: проверка, обогащение, отправка |
| `src/storage` | Интерфейс очереди и его реализация на Kafka |

Бизнес-логика (`services`) не импортирует ни Flask, ни Kafka: реализация
выбирается в одном месте — `api/dependencies.py`.

## Контракт событий

Общие поля у всех типов: `event_id` (генерирует клиент — по нему аналитика
убирает повторы), `session_id`, `occurred_at` (обязательно с часовым поясом) и
`client` (платформа, устройство, версия приложения). `user_id` в теле не
передаётся — его подставляет сервис из токена.

| `event_type` | Поля | Требование |
|---|---|---|
| `click` | `element_type`, `element_id`, `page`, `film_id` | ФТ-1 |
| `page_view` | `page`, `referrer`, `duration_ms` | ФТ-2 |
| `quality_changed` | `film_id`, `quality_from`, `quality_to`, `position_ms` | ФТ-3 |
| `video_completed` | `film_id`, `watched_ratio`, `duration_ms` | ФТ-3 |
| `search_filters_applied` | `query`, `filters`, `results_count` | ФТ-3 |

Полная схема с примерами — в [docs/openapi.json](docs/openapi.json) и на
http://localhost/ugc/api/openapi у запущенного стека.

## Эндпоинты

| Метод и путь | Что делает |
|---|---|
| `POST /ugc/api/v1/events` | Принимает пачку событий |
| `GET /ugc/api/v1/health` | Жив ли процесс (healthcheck контейнера) |
| `GET /ugc/api/v1/ready` | Готов ли сервис: есть ли соединение с брокером |
| `GET /ugc/api/openapi` | Документация |

Ответы `POST /events`:

| Код | Когда |
|---|---|
| 202 | Принято хотя бы одно событие; в теле — `accepted` и перечень `rejected` |
| 400 | Тело не разобрано как JSON или запрос пришёл мимо шлюза (нет `X-Request-Id`) |
| 401 | Токена нет, он истёк, подделан или это refresh-токен |
| 413 | В пачке больше событий, чем принимает сервис (`UGC_MAX_EVENTS_PER_REQUEST`) |
| 422 | Не принято ни одного события: все не прошли проверку контракта |
| 429 | Сработал предел частоты запросов в nginx |
| 503 | Брокер не принял события — клиенту следует повторить запрос |

Тело ошибки — `{"code": "...", "detail": "..."}`, как у сервиса авторизации.

Пример:

```bash
curl -X POST http://localhost/ugc/api/v1/events \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"events": [{
        "event_type": "click",
        "session_id": "d3f1b4c2-51f9-4a1f-9f0e-2b6c1a7e4d11",
        "occurred_at": "2026-09-21T19:04:11+03:00",
        "client": {"platform": "web"},
        "element_type": "film_card",
        "page": "/catalog/drama"
      }]}'
```

Токен берётся у сервиса авторизации: `POST /auth/api/v1/login`.

## Запуск

Сервис поднимается вместе со всем стеком кинотеатра:

```bash
docker compose up -d --build ugc
```

Локально, без Docker, — с уже поднятой Kafka:

```bash
pip install -r ugc/requirements.txt
cd ugc/src
export AUTH_JWT_SECRET_KEY=... UGC_KAFKA_BOOTSTRAP_SERVERS=localhost:9092
# Без nginx заголовок X-Request-Id ставить некому, поэтому проверку выключаем.
export UGC_REQUIRE_REQUEST_ID=false
gunicorn 'main:create_app()' --bind 127.0.0.1:8000 --worker-class gevent
```

Посмотреть, что доехало в брокер:

```bash
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
    --bootstrap-server localhost:9092 --topic ugc.events --from-beginning --max-messages 5
```

## Тесты

**Unit-тесты** — контракт событий, приём пачки на очереди в памяти, проверка
токена, адаптер Kafka с подменённым продюсером, настройки и актуальность
`docs/openapi.json`. Docker не требуется:

```bash
pip install -r ugc/tests/unit/requirements.txt
pytest ugc/tests/unit
```

**Функциональные тесты** — сервис в Docker со своей Kafka; тесты ходят к нему
по HTTP и читают записанное прямо из топика, кода сервиса не импортируя.
Проверяется каждый ответ каждого эндпоинта:

```bash
docker compose -f ugc/tests/functional/docker-compose.yml up --build \
    --abort-on-container-exit --exit-code-from tests
docker compose -f ugc/tests/functional/docker-compose.yml down -v
```

Код выхода — результат тестов. Порт сервиса (8002) и внешний порт брокера
(29092) проброшены на localhost, так что тесты можно запускать и из IDE:

```bash
docker compose -f ugc/tests/functional/docker-compose.yml up -d --build --wait ugc
pip install -r ugc/tests/functional/requirements.txt
pytest ugc/tests/functional
```

**Линтеры** — настройки в `setup.cfg` и `pyproject.toml`:

```bash
cd ugc && flake8 . && ruff check .
```

## Переменные окружения

У всех переменных сервиса префикс `UGC_`: `.env` общий для всех сервисов
кинотеатра. Исключение — ключ подписи токенов: он берётся из
`AUTH_JWT_SECRET_KEY`, потому что подпись проверяется тем же секретом, которым
выпущена.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `AUTH_JWT_SECRET_KEY` | — | Ключ подписи access-токенов, не короче 32 символов. Без него сервис не стартует |
| `UGC_KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Адреса брокеров через запятую |
| `UGC_KAFKA_TOPIC` | `ugc.events` | Топик событий |
| `UGC_KAFKA_ACKS` | `all` | Ждать записи во все синхронизированные реплики |
| `UGC_KAFKA_LINGER_MS` | `10` | Сколько продюсер копит сообщения перед отправкой |
| `UGC_KAFKA_BATCH_SIZE` | `65536` | Размер пачки продюсера, байт |
| `UGC_KAFKA_COMPRESSION` | `lz4` | Сжатие сообщений |
| `UGC_KAFKA_REQUEST_TIMEOUT_MS` | `5000` | Таймаут запроса к брокеру |
| `UGC_KAFKA_MAX_BLOCK_MS` | `5000` | Сколько ждать места в буфере и метаданных |
| `UGC_KAFKA_RETRIES` | `3` | Повторы отправки |
| `UGC_KAFKA_FLUSH_TIMEOUT` | `5` | Сколько ждать подтверждения записи пачки, секунд |
| `UGC_MAX_EVENTS_PER_REQUEST` | `200` | Предел размера пачки |
| `UGC_LOG_LEVEL` | `INFO` | Уровень журнала |
| `UGC_OTLP_ENDPOINT` | — | Куда отправлять спаны; пусто — трассировка выключена |
| `UGC_REQUIRE_REQUEST_ID` | `true` | Требовать `X-Request-Id` от шлюза |
| `WEB_CONCURRENCY` | `2` | Процессов gunicorn |
| `GEVENT_WORKER_CONNECTIONS` | `1000` | Зелёных потоков на процесс |
