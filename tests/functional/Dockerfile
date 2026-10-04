# Образ с функциональными тестами. Собирается из корня репозитория:
# тестам нужен пакет tests, код приложения в образ не попадает.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/tests

# Зависимости ставятся при сборке, а не при каждом запуске контейнера.
COPY tests/functional/requirements.txt tests/functional/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r tests/functional/requirements.txt

COPY tests/ tests/

ENTRYPOINT ["sh", "tests/functional/entrypoint.sh"]
