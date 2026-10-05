# Сервис уведомлений

Отправляет письма зрителям: о бронях и изменениях показов, о событиях других
сервисов и рассылки менеджеров. Сервисы присылают событие в API, дальше его
по очередям RabbitMQ разбирают воркеры: планировщик, сборщик (готовит письмо
по шаблону и данным получателя) и отправитель. Повтор события не даёт второго
письма, ночью письма ждут утра по поясу зрителя.

Рассылки менеджер создаёт в админке: http://localhost/admin/mailings/.

## Запуск

Сервис поднимается вместе со всем стеком из корня репозитория:

```bash
cp .env.example .env     # задайте AUTH_SERVICE_TOKEN, NOTIFY_POSTGRES_PASSWORD, RABBIT_PASSWORD
docker compose up -d --build
```

* документация API — http://localhost/notify/api/openapi;
* письма стенда — http://localhost:8025 (Mailpit, наружу ничего не уходит);
* очереди брокера — http://localhost:15672.

Тесты:

```bash
cd notifications/tests/unit && pytest
docker compose -f notifications/tests/functional/docker-compose.yml up --build \
    --abort-on-container-exit --exit-code-from tests
```
