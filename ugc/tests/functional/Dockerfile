# Образ с функциональными тестами. Собирается из каталога ugc: тестам нужен
# пакет tests, код сервиса в образ не попадает — тесты ходят к нему по HTTP.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Таймаут чтения у pip по умолчанию 15 секунд: на медленном канале
    # крупные пакеты не докачиваются и сборка падает на ровном месте.
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /opt/tests

COPY tests/functional/requirements.txt tests/functional/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r tests/functional/requirements.txt

COPY tests/__init__.py tests/__init__.py
COPY tests/functional/ tests/functional/

ENTRYPOINT ["sh", "tests/functional/entrypoint.sh"]
