# ETL каталога

Переносит фильмы, жанры и персоны из PostgreSQL в Elasticsearch, откуда их
читает Async API. Изменения находит по журналу аудита и забирает пачками,
состояние хранит в базе: после перезапуска продолжает с места остановки, а при
недоступности PostgreSQL или Elasticsearch ждёт с нарастающей паузой.

База каталога разворачивается из дампа `etl/dump.sql.gz` при первом старте
контейнера `postgres`.

## Запуск

ETL поднимается вместе со всем стеком из корня репозитория:

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f etl
```

Тесты:

```bash
cd etl && python -m pytest tests
```
