-- Колонки каталога, которых нет в исходном дампе. Выполняется при каждом
-- старте ETL и поэтому идемпотентна: ADD COLUMN IF NOT EXISTS ничего не
-- делает на уже обновлённой базе.
--
-- Обложка и идентификатор IMDb (ADR-25 в docs/diploma/architecture.md): сама
-- картинка лежит у источника, в каталоге — только ссылка на неё. Редактор
-- может поправить её в админке.
ALTER TABLE content.film_work ADD COLUMN IF NOT EXISTS poster_url TEXT;
ALTER TABLE content.film_work ADD COLUMN IF NOT EXISTS imdb_id VARCHAR(16);

-- Данные Кинопоиска о фильме: что нашёл поиск по названию. Строка заводится
-- и для ненайденного фильма (found = false): лимит ключа — 500 запросов в
-- сутки, и тратить запрос на него повторно незачем. Полный ответ API лежит в
-- raw — из него можно достать поле, которое понадобится потом, не спрашивая
-- Кинопоиск ещё раз.
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
    poster_url     TEXT,
    raw            JSONB,
    fetched_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- Обложки — сами картинки, а не ссылки на чужие сайты: страница не зависит
-- от того, жив ли чужой CDN и пускает ли он к себе. Отдаёт их админка по
-- адресу /posters/<film_id>.jpg, nginx кеширует.
CREATE TABLE IF NOT EXISTS content.film_poster (
    film_id      UUID PRIMARY KEY REFERENCES content.film_work (id) ON DELETE CASCADE,
    content      BYTEA NOT NULL,
    content_type VARCHAR(64) NOT NULL,
    source       VARCHAR(16) NOT NULL,
    source_url   TEXT NOT NULL,
    fetched_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);
