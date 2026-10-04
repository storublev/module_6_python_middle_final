FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/etl

COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" etluser
USER etluser

ENTRYPOINT ["./entrypoint.sh"]