#!/bin/bash
# etl/entrypoint.sh

set -e

echo "🚀 Запуск ETL сервиса..."

# Получаем переменные с дефолтными значениями
DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-app}"
DB_PASSWORD="${DB_PASSWORD:-123qwe}"
DB_NAME="${DB_NAME:-movies_database}"
ES_HOST="${ELASTIC_HOST:-http://elastic:9200}"

echo "📌 PostgreSQL: $DB_HOST:$DB_PORT"
echo "📌 Elasticsearch: $ES_HOST"

# Функция ожидания PostgreSQL
wait_for_postgres() {
    echo "⏳ Ожидание PostgreSQL..."
    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if PGPASSWORD="$DB_PASSWORD" pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" 2>/dev/null; then
            echo "✅ PostgreSQL готов"
            return 0
        fi
        echo "⏳ Попытка $attempt/$max_attempts..."
        sleep 2
        attempt=$((attempt + 1))
    done

    echo "❌ PostgreSQL не доступен"
    return 1
}

# Функция ожидания Elasticsearch
wait_for_elasticsearch() {
    echo "⏳ Ожидание Elasticsearch..."
    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if curl -s "$ES_HOST" >/dev/null 2>&1; then
            echo "✅ Elasticsearch готов"
            return 0
        fi
        echo "⏳ Попытка $attempt/$max_attempts..."
        sleep 2
        attempt=$((attempt + 1))
    done

    echo "❌ Elasticsearch не доступен"
    return 1
}
# Функция для инициализации базы данных
init_database() {
    echo "📝 Инициализация базы данных..."

    # Проверяем, существует ли таблица audit_log
    local has_audit=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -tAc \
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'content' AND table_name = 'audit_log')")

    if [ "$has_audit" = "t" ]; then
        echo "✅ Таблицы уже существуют, пропускаем инициализацию"
        return 0
    fi

    echo "📝 Создание таблиц из /opt/etl/sql/init.sql..."

    if [ -f /opt/etl/sql/init.sql ]; then
        PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -f /opt/etl/sql/init.sql
        echo "✅ Таблицы успешно созданы"
    else
        echo "❌ Файл /opt/etl/sql/init.sql не найден!"
        echo "📁 Содержимое /opt/etl/sql/:"
        ls -la /opt/etl/sql/ || echo "Директория пуста"
        return 1
    fi
}

# Ждем сервисы
wait_for_postgres || exit 1
wait_for_elasticsearch || exit 1

# Инициализируем базу данных
init_database || exit 1

echo "========================================="
echo "✅ Все сервисы готовы, запускаю ETL..."
echo "========================================="

# Запускаем ETL
exec python -u /opt/etl/etl.py