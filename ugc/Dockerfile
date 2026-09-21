FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Таймаут чтения у pip по умолчанию 15 секунд: на медленном канале
    # крупные пакеты не докачиваются и сборка падает на ровном месте.
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    # Количество процессов gunicorn. Каждый держит свой цикл gevent и свой
    # продюсер Kafka, поэтому процессов должно быть примерно по числу ядер.
    WEB_CONCURRENCY=2 \
    # Сколько зелёных потоков обслуживает один процесс. Запрос почти всё время
    # ждёт подтверждения от брокера, поэтому потоков сильно больше, чем ядер:
    # пока один ждёт, остальные работают.
    GEVENT_WORKER_CONNECTIONS=1000

WORKDIR /opt/app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY src/ .

RUN useradd --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000

# Приложение создаётся фабрикой: в ней открывается соединение с Kafka, и
# делать это при импорте модуля незачем. Воркер gevent подменяет сокеты
# стандартной библиотеки до импорта приложения.
CMD ["sh", "-c", "exec gunicorn 'main:create_app()' \
    --bind 0.0.0.0:8000 \
    --worker-class gevent \
    --worker-connections ${GEVENT_WORKER_CONNECTIONS} \
    --access-logfile - \
    --graceful-timeout 10"]
