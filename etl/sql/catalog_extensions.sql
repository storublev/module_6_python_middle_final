-- Колонки каталога, которых нет в исходном дампе. Выполняется при каждом
-- старте ETL и поэтому идемпотентна: ADD COLUMN IF NOT EXISTS ничего не
-- делает на уже обновлённой базе.
--
-- Обложка и идентификатор IMDb (ADR-25 в docs/diploma/architecture.md): сама
-- картинка лежит у источника, в каталоге — только ссылка на неё. Редактор
-- может поправить её в админке.
ALTER TABLE content.film_work ADD COLUMN IF NOT EXISTS poster_url TEXT;
ALTER TABLE content.film_work ADD COLUMN IF NOT EXISTS imdb_id VARCHAR(16);
