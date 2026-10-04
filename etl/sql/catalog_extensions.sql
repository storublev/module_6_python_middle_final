-- Колонки и таблицы каталога сверх исходного дампа. Выполняется при каждом
-- старте ETL и поэтому идемпотентна.
--
-- Обложки и данные Кинопоиска хранятся в самой базе (ADR-25 в
-- docs/diploma/architecture.md). Адресов сторонних ресурсов база не хранит:
-- картинка скачивается сразу в content.film_poster, а откуда она взята, не
-- запоминается.

-- Идентификатор фильма на IMDb — не ссылка, а ключ сопоставления.
ALTER TABLE content.film_work ADD COLUMN IF NOT EXISTS imdb_id VARCHAR(16);

-- Данные Кинопоиска о фильме: что нашёл поиск по названию. Строка заводится
-- и для ненайденного фильма (found = false): лимит ключа — 500 запросов в
-- сутки, и тратить запрос на него повторно незачем. Ответ API лежит в raw без
-- адресов картинок.
CREATE TABLE IF NOT EXISTS content.film_kinopoisk (
    film_id        UUID PRIMARY KEY REFERENCES content.film_work (id) ON DELETE CASCADE,
    found          BOOLEAN NOT NULL,
    kinopoisk_id   INTEGER,
    title_ru       TEXT,
    description_ru TEXT,
    year           SMALLINT,
    rating         REAL,
    rating_votes   INTEGER,
    length         TEXT,
    countries      TEXT[],
    genres         TEXT[],
    raw            JSONB,
    fetched_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- Картинки обложек. Отдаёт их админка по адресу /posters/<film_id>.jpg,
-- nginx кеширует; загружает — редактор в админке или скрипты ETL.
CREATE TABLE IF NOT EXISTS content.film_poster (
    film_id      UUID PRIMARY KEY REFERENCES content.film_work (id) ON DELETE CASCADE,
    content      BYTEA NOT NULL,
    content_type VARCHAR(64) NOT NULL,
    fetched_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- База, созданная до отказа от сторонних адресов: убрать ссылки и сведения о
-- том, откуда взята картинка, — из таблиц, из ответа Кинопоиска и из журнала
-- аудита ETL. На обновлённой базе эти команды ничего не делают.
ALTER TABLE content.film_work DROP COLUMN IF EXISTS poster_url;
ALTER TABLE content.film_poster DROP COLUMN IF EXISTS source;
ALTER TABLE content.film_poster DROP COLUMN IF EXISTS source_url;
ALTER TABLE content.film_kinopoisk DROP COLUMN IF EXISTS poster_url;
UPDATE content.film_kinopoisk
   SET raw = raw - 'posterUrl' - 'posterUrlPreview'
 WHERE raw ?| ARRAY['posterUrl', 'posterUrlPreview'];
DO $$
BEGIN
    IF to_regclass('content.audit_log') IS NOT NULL THEN
        UPDATE content.audit_log
           SET old_data = old_data - 'poster_url', new_data = new_data - 'poster_url'
         WHERE old_data ? 'poster_url' OR new_data ? 'poster_url';
    END IF;
END $$;
