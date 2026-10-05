# Сервис бронирования билетов

Дипломный проект: совместные просмотры. Хост предлагает фильм, место, дату и
время и число мест; гость выбирает хоста, затем дату и время и бронирует
места. **Забронировать больше мест, чем есть у хоста, нельзя** — ни одним
запросом, ни одновременными запросами разных гостей. После показа хост и
гости оценивают друг друга (задание со звёздочкой).

Требования, архитектура и ADR — [docs/diploma/](../docs/diploma/).

## Стек

FastAPI · SQLAlchemy 2 (asyncpg) · Alembic · PostgreSQL 16 · httpx · PyJWT.

Процессы из одного образа:

* `booking-api` — HTTP API, документация — http://localhost/booking/api/openapi;
* `booking-relay` — ретранслятор outbox: события о бронях → API уведомлений.

## Вход и запись

Читать показы и рейтинги можно без входа; кабинет читается с access-токеном,
который проверяется на месте (подпись и срок). **Любая запись** перед
выполнением сверяет сессию токена с сервисом авторизации (`GET /users/me`):
после выхода или смены пароля старый токен получает 401 `token_revoked`, а без
ответа Auth запись отклоняется с 503 (ADR-27). В nginx частота любой записи
(`POST`, `PATCH` и т.д.) в `/booking/` ограничена 5 запросами в секунду с IP.

## Как держится гарантия мест

Бронь — одна транзакция: условный `UPDATE screenings SET seats_taken =
seats_taken + n WHERE … AND seats_taken + n <= capacity`, вставка брони
(частичный уникальный индекс: одна активная бронь гостя на показ) и событие в
outbox. Последний рубеж — `CHECK (seats_taken <= capacity)` в таблице.
Почему именно так — исследование [research/overbooking.py](research/overbooking.py)
и [docs/diploma/research.md](../docs/diploma/research.md): проверка остатка в коде
продала 277 мест из 10.

Строки блокируются всегда в одном порядке — **показ, затем бронь** (изменение
и отмена брони, отмена показа), иначе одновременные действия хоста и гостя
сцеплялись бы во взаимной блокировке. Имя гостя из справочника Auth берётся до
первого запроса к базе, чтобы ожидание соседа не держало соединение пула.

## Отклонённые события

Если сервис уведомлений отверг событие по существу (4xx), ретранслятор его не
удаляет: событие остаётся в outbox с `rejected_at` и причиной отказа и больше
не отправляется само. После исправления причины его возвращают в отправку с
тем же `event_id`:

```bash
docker compose exec booking-relay python outbox_cli.py list
docker compose exec booking-relay python outbox_cli.py requeue --all
docker compose exec booking-relay python outbox_cli.py requeue --id <event_id>
```

## Эндпоинты

| Метод и путь | Кто | Что делает |
|---|---|---|
| `GET /films/{film_id}/hosts` | все | Хосты фильма с рейтингом и ближайшим показом |
| `GET /screenings?film_id=&host_id=` | все | Будущие показы |
| `GET /screenings/{id}` | все | Показ |
| `POST /screenings` | вошедший | Создать показ |
| `PATCH /screenings/{id}` | хост | Изменить показ |
| `POST /screenings/{id}/cancel` | хост | Отменить показ и все брони |
| `GET /screenings/{id}/bookings` | хост | Гости с рейтингом |
| `GET /screenings/{id}/bookings/mine` | вошедший | Моя бронь на показе |
| `POST /screenings/{id}/bookings` | вошедший | Забронировать места |
| `PATCH /bookings/{id}` | гость | Изменить число мест |
| `POST /bookings/{id}/cancel` | гость | Отменить бронь |
| `GET /me/screenings`, `GET /me/bookings` | вошедший | Расписание хоста и брони гостя |
| `POST /screenings/{id}/ratings` | участник | Оценить хоста или гостя |
| `GET /screenings/{id}/ratings/mine` | участник | Кого я уже оценил |
| `GET /users/{id}/rating`, `GET /users/{id}/reviews` | все | Рейтинг и отзывы |

Префикс — `/booking/api/v1`. Ошибки — `{"code": "...", "detail": "..."}`:
`not_enough_seats`, `already_booked`, `own_screening`, `screening_closed`,
`film_not_bookable` и другие; у каждого эндпоинта все они описаны в OpenAPI.

## Настройки

Префикс `BOOKING_`, полный список — [src/core/config.py](src/core/config.py).

| Переменная | Что задаёт | По умолчанию |
|---|---|---|
| `BOOKING_POSTGRES_*` | база | — (пароль обязателен) |
| `AUTH_JWT_SECRET_KEY` | ключ подписи токенов, общий с Auth | — (обязателен) |
| `AUTH_SERVICE_TOKEN` | служебный секрет: справочник имён и API уведомлений | — |
| `BOOKING_CATALOG_URL`, `BOOKING_AUTH_URL`, `BOOKING_NOTIFY_URL` | соседние сервисы | адреса в compose |
| `BOOKING_MIN_CAPACITY`…`BOOKING_MAX_CAPACITY` | мест на показ | 1…50 |
| `BOOKING_MAX_SEATS_PER_BOOKING` | мест в одной брони | 10 |
| `BOOKING_MIN_LEAD_TIME`, `BOOKING_MAX_LEAD_TIME` | когда можно назначить показ, секунды | 1800, год |
| `BOOKING_DISPLAY_TIMEZONE` | пояс времени в письмах | Europe/Moscow |

## Тесты

```bash
# unit: бизнес-логика на хранилищах в памяти и HTTP-слой
cd booking/tests/unit && python -m pytest

# функциональные: настоящие PostgreSQL и сервис авторизации, заглушка каталога и уведомлений
docker compose -f booking/tests/functional/docker-compose.yml up --build \
    --abort-on-container-exit --exit-code-from tests

# исследование защиты от перебронирования (нужен пустой PostgreSQL)
python booking/research/overbooking.py --dsn postgresql://booking:booking@127.0.0.1:5432/booking
```
