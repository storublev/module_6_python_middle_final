# Сервис авторизации

Регистрация, вход и выход, обновление токенов, вход через Яндекс и Google,
личный кабинет (профиль, смена логина и пароля, история входов) и роли с
правами. Выдаёт JWT: access-токен на 15 минут и refresh-токен на 14 дней;
остальные сервисы проверяют access-токен его подписью.

## Запуск

Сервис поднимается вместе со всем стеком из корня репозитория:

```bash
cp .env.example .env     # задайте AUTH_POSTGRES_PASSWORD и AUTH_JWT_SECRET_KEY (не короче 32 символов)
docker compose up -d --build
docker compose exec auth python cli.py createsuperuser --login admin
```

Документация API — http://localhost/auth/api/openapi.

Тесты:

```bash
pytest auth/tests/unit
docker compose -f auth/tests/functional/docker-compose.yml up --build \
    --abort-on-container-exit --exit-code-from tests
```
