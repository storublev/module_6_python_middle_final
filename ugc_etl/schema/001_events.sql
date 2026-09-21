-- Схема аналитического хранилища событий.
--
-- Применяется одноразовым контейнером при запуске стека; файл идемпотентный,
-- поэтому повторный запуск ничего не ломает.
--
-- Почему колонки плоские, а не один JSON: ClickHouse хранит данные по
-- колонкам и читает только те, что нужны запросу. Запрос «самые
-- просматриваемые фильмы» тронет две колонки из двадцати, а не разберёт
-- двадцать миллионов JSON-ов.

CREATE DATABASE IF NOT EXISTS ugc;

CREATE TABLE IF NOT EXISTS ugc.events
(
    -- Идентификатор события генерирует клиент; по нему убираются повторы,
    -- неизбежные при доставке at-least-once.
    event_id        UUID,
    event_type      LowCardinality(String),
    -- Время на стороне клиента и время приёма сервисом: по их расхождению
    -- видно, насколько врут часы клиента и долго ли событие ждало отправки.
    occurred_at     DateTime64(3, 'UTC'),
    received_at     DateTime64(3, 'UTC'),
    user_id         UUID,
    session_id      UUID,

    -- Контекст события. Пустая строка вместо NULL: Nullable в ClickHouse
    -- хранит отдельную колонку с признаком и хуже сжимается, а «нет фильма» и
    -- «пустой фильм» для аналитики одно и то же.
    film_id         UUID,
    page            String,
    referrer        String,
    element_type    LowCardinality(String),
    element_id      String,

    -- Поля отдельных типов событий: у события своего типа заполнены свои.
    duration_ms     UInt32,
    position_ms     UInt32,
    watched_ratio   Float32,
    quality_from    LowCardinality(String),
    quality_to      LowCardinality(String),
    search_query    String,
    filters         Map(LowCardinality(String), String),
    results_count   UInt32,

    -- Клиент: по этим колонкам аналитика режет данные по платформам.
    platform        LowCardinality(String),
    device          String,
    app_version     String
)
-- ReplacingMergeTree схлопывает строки с одинаковым ключом сортировки при
-- слиянии кусков: повтор пачки после падения ETL не удваивает событие.
-- Слияния идут в фоне, поэтому запросы, которым дубликаты недопустимы,
-- добавляют FINAL или группируют по event_id.
ENGINE = ReplacingMergeTree
-- Месячные партиции: хранение трёхлетнее, и старый месяц удаляется одним
-- DROP PARTITION, не перебирая строки.
PARTITION BY toYYYYMM(occurred_at)
-- Ключ сортировки начинается с типа события: почти каждый аналитический
-- запрос фильтрует по нему, а внутри типа данные идут по времени — так
-- запрос за неделю читает несколько гранул вместо всей таблицы.
ORDER BY (event_type, occurred_at, event_id)
TTL toDateTime(occurred_at) + INTERVAL 3 YEAR;

-- Витрина популярности фильмов по дням. Агрегаты досчитываются при вставке,
-- поэтому вопрос «самые просматриваемые фильмы за месяц» читает тысячи строк
-- витрины вместо сотен миллионов строк событий.
CREATE TABLE IF NOT EXISTS ugc.film_daily
(
    day             Date,
    film_id         UUID,
    views           AggregateFunction(count, UInt8),
    viewers         AggregateFunction(uniq, UUID),
    -- Средняя доля просмотра: по ней видно, какие фильмы не досматривают.
    watched_ratio   AggregateFunction(avg, Float32)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(day)
ORDER BY (day, film_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS ugc.film_daily_mv TO ugc.film_daily AS
SELECT
    toDate(occurred_at) AS day,
    film_id,
    countState(toUInt8(1))      AS views,
    uniqState(user_id)          AS viewers,
    avgState(watched_ratio)     AS watched_ratio
FROM ugc.events
-- Витрина считается по досмотрам: только у них есть доля просмотра.
WHERE event_type = 'video_completed'
GROUP BY day, film_id;
