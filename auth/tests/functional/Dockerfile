# Образ с функциональными тестами. Собирается из каталога auth: тестам нужен
# пакет tests, код сервиса в образ не попадает.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/tests

COPY tests/functional/requirements.txt tests/functional/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r tests/functional/requirements.txt

COPY tests/__init__.py tests/__init__.py
COPY tests/functional/ tests/functional/

ENTRYPOINT ["sh", "tests/functional/entrypoint.sh"]
