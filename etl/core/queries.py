"""SQL-запросы для извлечения данных из PostgreSQL."""

# Первичный ключ страницы записей таблицы (keyset-пагинация для начальной загрузки).
# Имя таблицы подставляется из конфигурации пайплайна, а не из внешних данных.
FETCH_IDS_PAGE = """
SELECT id
FROM content.{table}
WHERE id > %(last_id)s
ORDER BY id
LIMIT %(limit)s
"""

# Фильмы по списку ID
FETCH_MOVIES_BY_IDS = """
SELECT fw.id,
       fw.rating AS imdb_rating,
       fw.title,
       fw.description,
       fw.modified,
       fw.creation_date,
       fw.type,
       -- Загруженная в базу картинка важнее внешней ссылки: её отдаёт сам
       -- кинотеатр (scripts/fetch_poster_images.py).
       CASE WHEN fp.film_id IS NOT NULL THEN '/posters/' || fw.id || '.jpg'
            ELSE fw.poster_url END AS poster_url,
       fw.imdb_id,
       kp.kinopoisk_id,
       kp.title_ru,
       kp.description_ru,
       kp.rating AS kinopoisk_rating,
       kp.year,
       COALESCE(
           json_agg(
               DISTINCT jsonb_build_object('id', g.id, 'name', g.name)
           ) FILTER (WHERE g.id IS NOT NULL),
           '[]'::json
       ) AS genres,
       COALESCE(
           json_agg(
               DISTINCT jsonb_build_object(
                   'id', p.id,
                   'name', p.full_name,
                   'role', pfw.role
               )
           ) FILTER (WHERE p.id IS NOT NULL),
           '[]'::json
       ) AS persons
FROM content.film_work fw
LEFT JOIN content.genre_film_work  gfw  ON gfw.film_work_id = fw.id
LEFT JOIN content.genre            g    ON g.id = gfw.genre_id
LEFT JOIN content.person_film_work pfw  ON pfw.film_work_id = fw.id
LEFT JOIN content.person           p    ON p.id = pfw.person_id
LEFT JOIN content.film_kinopoisk   kp   ON kp.film_id = fw.id AND kp.found
LEFT JOIN content.film_poster      fp   ON fp.film_id = fw.id
WHERE fw.id = ANY(%(ids)s::uuid[])
GROUP BY fw.id, fw.rating, fw.title, fw.description, fw.modified, fw.creation_date,
         fw.type, fw.poster_url, fw.imdb_id, fp.film_id, kp.kinopoisk_id, kp.title_ru,
         kp.description_ru, kp.rating, kp.year
"""

# Жанры по списку ID — только те, что есть хотя бы в одном фильме
FETCH_GENRES_BY_IDS = """
SELECT g.id,
       g.name,
       g.description
FROM content.genre g
WHERE g.id = ANY(%(ids)s::uuid[])
  AND EXISTS (
      SELECT 1 FROM content.genre_film_work gfw WHERE gfw.genre_id = g.id
  )
"""

# Персоны по списку ID с их фильмами и ролями в каждом фильме.
# Персоны без фильмов в выборку не попадают (INNER JOIN).
FETCH_PERSONS_BY_IDS = """
SELECT p.id,
       p.full_name,
       json_agg(
           json_build_object('id', pf.film_work_id, 'roles', pf.roles)
           ORDER BY pf.film_work_id
       ) AS films
FROM content.person p
JOIN (
    SELECT pfw.person_id,
           pfw.film_work_id,
           array_agg(DISTINCT pfw.role ORDER BY pfw.role) AS roles
    FROM content.person_film_work pfw
    WHERE pfw.person_id = ANY(%(ids)s::uuid[])
    GROUP BY pfw.person_id, pfw.film_work_id
) pf ON pf.person_id = p.id
WHERE p.id = ANY(%(ids)s::uuid[])
GROUP BY p.id, p.full_name
"""

# Фильмы, в которые входят жанры / персоны: при переименовании жанра или
# персоны нужно переиндексировать все их фильмы.
FETCH_FILM_IDS_BY_GENRES = """
SELECT DISTINCT film_work_id AS id
FROM content.genre_film_work
WHERE genre_id = ANY(%(ids)s::uuid[])
"""

FETCH_FILM_IDS_BY_PERSONS = """
SELECT DISTINCT film_work_id AS id
FROM content.person_film_work
WHERE person_id = ANY(%(ids)s::uuid[])
"""

# Фильмы, у которых срок «новинки» истёк после since и не позже until:
# их метку доступа нужно сменить с subscription на public.
FETCH_FILM_IDS_LEAVING_SUBSCRIPTION = """
SELECT id
FROM content.film_work
WHERE creation_date > %(since)s
  AND creation_date <= %(until)s
"""

# Запрос для получения измененных фильмов (с пагинацией)
FETCH_MODIFIED_MOVIES = """
WITH recently_modified AS (
    SELECT DISTINCT fw.id
    FROM content.film_work fw
    LEFT JOIN content.genre_film_work  gfw ON gfw.film_work_id = fw.id
    LEFT JOIN content.genre            g   ON g.id = gfw.genre_id
    LEFT JOIN content.person_film_work pfw ON pfw.film_work_id = fw.id
    LEFT JOIN content.person           p   ON p.id = pfw.person_id
    WHERE fw.modified > '{last_modified}'
       OR g.modified   > '{last_modified}'
       OR p.modified   > '{last_modified}'
),
ordered_movies AS (
    SELECT fw.id,
           fw.rating            AS imdb_rating,
           fw.title,
           fw.description,
           fw.modified,
           fw.creation_date,
           fw.type,
           fw.poster_url,
           fw.imdb_id,
           ROW_NUMBER() OVER (ORDER BY fw.modified, fw.id) AS row_num
    FROM content.film_work fw
    WHERE fw.id IN (SELECT id FROM recently_modified)
)
SELECT om.id,
       om.imdb_rating,
       om.title,
       om.description,
       om.modified,
       om.creation_date,
       om.type,
       om.poster_url,
       om.imdb_id,
       COALESCE(
           json_agg(
               DISTINCT jsonb_build_object('id', g.id, 'name', g.name)
           ) FILTER (WHERE g.id IS NOT NULL),
           '[]'::json
       ) AS genres,
       COALESCE(
           json_agg(
               DISTINCT jsonb_build_object(
                   'id', p.id,
                   'name', p.full_name,
                   'role', pfw.role
               )
           ) FILTER (WHERE p.id IS NOT NULL),
           '[]'::json
       ) AS persons
FROM ordered_movies om
LEFT JOIN content.genre_film_work  gfw  ON gfw.film_work_id = om.id
LEFT JOIN content.genre            g    ON g.id = gfw.genre_id
LEFT JOIN content.person_film_work pfw  ON pfw.film_work_id = om.id
LEFT JOIN content.person           p    ON p.id = pfw.person_id
WHERE om.row_num > {offset} AND om.row_num <= {offset} + {limit}
GROUP BY om.id, om.imdb_rating, om.title, om.description, om.modified, om.creation_date,
         om.type, om.poster_url, om.imdb_id
ORDER BY om.modified, om.id
"""

# Запрос для получения количества измененных фильмов
COUNT_MODIFIED_MOVIES = """
SELECT COUNT(DISTINCT fw.id)
FROM content.film_work fw
LEFT JOIN content.genre_film_work  gfw ON gfw.film_work_id = fw.id
LEFT JOIN content.genre            g   ON g.id = gfw.genre_id
LEFT JOIN content.person_film_work pfw ON pfw.film_work_id = fw.id
LEFT JOIN content.person           p   ON p.id = pfw.person_id
WHERE fw.modified > '{last_modified}'
   OR g.modified   > '{last_modified}'
   OR p.modified   > '{last_modified}'
"""
