FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    # Воркеров gunicorn: админкой пользуются единицы сотрудников.
    WEB_CONCURRENCY=2

WORKDIR /opt/app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY manage.py .
COPY config/ config/
COPY movies/ movies/
COPY users/ users/
# Страницы рассылок: шаблоны писем и запуск рассылок для менеджера.
COPY campaigns/ campaigns/

# Статика админки собирается в образ, а раздаёт её nginx из общего тома:
# одноразовый контейнер копирует её туда при запуске стека.
RUN useradd --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p staticfiles \
    && chown -R app:app /opt/app
USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--access-logfile", "-"]
