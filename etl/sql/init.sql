-- sql/init.sql

CREATE TABLE IF NOT EXISTS content.audit_log (
    id BIGSERIAL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    record_id VARCHAR(36) NOT NULL,
    operation CHAR(1) NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    old_data JSONB,
    new_data JSONB,
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_processed ON content.audit_log(processed, changed_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_record ON content.audit_log(record_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_table ON content.audit_log(table_name);

-- 2. Таблица состояния
CREATE TABLE IF NOT EXISTS content.etl_state (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_etl_state_key ON content.etl_state(key);

-- 3. Функции и триггеры для аудита
CREATE OR REPLACE FUNCTION content.audit_trigger_function()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO content.audit_log (table_name, record_id, operation, new_data)
        VALUES (TG_TABLE_NAME, NEW.id, 'I', to_jsonb(NEW));
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO content.audit_log (table_name, record_id, operation, old_data, new_data)
        VALUES (TG_TABLE_NAME, NEW.id, 'U', to_jsonb(OLD), to_jsonb(NEW));
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO content.audit_log (table_name, record_id, operation, old_data)
        VALUES (TG_TABLE_NAME, OLD.id, 'D', to_jsonb(OLD));
        RETURN OLD;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- 4. Функция для обновления updated_at в etl_state
CREATE OR REPLACE FUNCTION content.update_etl_state_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 5. Триггеры для etl_state
DROP TRIGGER IF EXISTS update_etl_state_updated_at ON content.etl_state;
CREATE TRIGGER update_etl_state_updated_at
    BEFORE UPDATE ON content.etl_state
    FOR EACH ROW
    EXECUTE FUNCTION content.update_etl_state_updated_at();

-- 6. Триггеры для таблиц
DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOR table_name IN
        SELECT unnest(ARRAY['film_work', 'person', 'genre', 'genre_film_work', 'person_film_work'])
    LOOP
        EXECUTE format('
            DROP TRIGGER IF EXISTS audit_%I_trigger ON content.%I;
            CREATE TRIGGER audit_%I_trigger
            AFTER INSERT OR UPDATE OR DELETE ON content.%I
            FOR EACH ROW EXECUTE FUNCTION content.audit_trigger_function();
        ', table_name, table_name, table_name, table_name);
    END LOOP;
END $$;

-- 7. Начальные значения для состояния
INSERT INTO content.etl_state (key, value) VALUES
    ('last_audit_id', '0'),
    ('last_modified', '1970-01-01 00:00:00.000000'),
    ('statistics', '{"total_processed": 0, "total_runs": 0}')
ON CONFLICT (key) DO NOTHING;

-- 8. Инициализация существующих данных в аудит-логе
-- Помечаем все существующие фильмы как обработанные (processed = TRUE)
-- чтобы не дублировать их при первом запуске
--INSERT INTO content.audit_log (table_name, record_id, operation, new_data, processed)
--SELECT
--    'film_work',
--    fw.id,
--    'I',
--    to_jsonb(fw.*),
--    TRUE  -- Сразу помечаем как обработанные
--FROM content.film_work fw
--WHERE NOT EXISTS (
--    SELECT 1 FROM content.audit_log al
--    WHERE al.record_id = fw.id
--    AND al.table_name = 'film_work'
--);