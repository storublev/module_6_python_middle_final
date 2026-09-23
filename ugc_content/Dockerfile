FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Таймаут чтения у pip по умолчанию 15 секунд: на медленном канале
    # крупные пакеты не докачиваются и сборка падает на ровном месте.
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    # Сколько процессов uvicorn. Сервис почти всё время ждёт MongoDB, поэтому
    # процессов хватает по числу ядер: параллелизм внутри процесса даёт
    # асинхронность, а не потоки.
    WEB_CONCURRENCY=2

WORKDIR /opt/app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY src/ .

RUN useradd --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000

# Приложение создаётся фабрикой: в ней открывается соединение с MongoDB, и
# делать это при импорте модуля незачем.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY} --no-access-log"]
