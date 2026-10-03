-- Загрузка обложек, найденных скриптом scripts/fetch_posters.py.
--
-- Заполняются только пустые обложки: правку редактора из админки файл не
-- затирает, а повторный старт ETL ничего не меняет. Изменение строки фильма
-- попадает в журнал аудита, и ETL сам переносит обложку в индекс.
CREATE TEMP TABLE posters (film_id UUID PRIMARY KEY, imdb_id VARCHAR(16), poster_url TEXT NOT NULL);
\copy posters FROM '/opt/etl/data/posters.csv' WITH (FORMAT csv, HEADER true)
UPDATE content.film_work AS fw
   SET poster_url = p.poster_url,
       imdb_id = COALESCE(fw.imdb_id, p.imdb_id),
       modified = now()
  FROM posters AS p
 WHERE fw.id = p.film_id
   AND fw.poster_url IS NULL;
