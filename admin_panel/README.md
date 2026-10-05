# Админка

Django-админка кинотеатра: фильмы (с загрузкой обложки), жанры, персоны и
раздел «Рассылки» с шаблонами писем. Сотрудники входят учётной записью сервиса
авторизации; доступ даёт роль `staff` или суперпользователь.

## Запуск

Админка поднимается вместе со всем стеком из корня репозитория:

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec auth python cli.py createsuperuser --login admin
```

Админка открывается на http://localhost/admin/ — войти логином и паролем,
заданными командой выше.

Тесты:

```bash
cd admin_panel/tests/unit && pytest
```
