-- Тип фильма живёт в схеме public, а дамп каталога (etl/dump.sql.gz) выгружен
-- только по схеме content: public целиком в репозиторий не кладём — там
-- таблицы админки с данными сотрудников. Поэтому тип заводится отдельно и
-- раньше дампа: файлы /docker-entrypoint-initdb.d выполняются по алфавиту.
CREATE TYPE public.film_type AS ENUM (
    'movie',
    'tv_show'
);
