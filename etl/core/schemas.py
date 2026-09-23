"""Схемы индексов Elasticsearch."""

MOVIES_INDEX = "movies"

INDEX_SETTINGS = {
    "refresh_interval": "1s",
    "analysis": {
        "filter": {
            "english_stop": {"type": "stop", "stopwords": "_english_"},
            "english_stemmer": {"type": "stemmer", "language": "english"},
            "english_possessive_stemmer": {
                "type": "stemmer",
                "language": "possessive_english",
            },
            "russian_stop": {"type": "stop", "stopwords": "_russian_"},
            "russian_stemmer": {"type": "stemmer", "language": "russian"},
        },
        "analyzer": {
            "ru_en": {
                "tokenizer": "standard",
                "filter": [
                    "lowercase",
                    "english_stop",
                    "english_stemmer",
                    "english_possessive_stemmer",
                    "russian_stop",
                    "russian_stemmer",
                ],
            }
        },
    },
}

PERSON_MAPPING = {
    "type": "nested",
    "dynamic": "strict",
    "properties": {
        "id": {"type": "keyword"},
        "name": {"type": "text", "analyzer": "ru_en"},
    },
}

GENRE_MAPPING = {
    "type": "nested",
    "dynamic": "strict",
    "properties": {
        "id": {"type": "keyword"},
        "name": {"type": "text", "analyzer": "ru_en"},
    },
}

MOVIES_MAPPING = {
    "dynamic": "strict",
    "properties": {
        "id": {"type": "keyword"},
        "imdb_rating": {"type": "float"},
        "genres": GENRE_MAPPING,
        "title": {
            "type": "text",
            "analyzer": "ru_en",
            "fields": {"raw": {"type": "keyword"}},
        },
        "description": {"type": "text", "analyzer": "ru_en"},
        "directors": PERSON_MAPPING,
        "actors": PERSON_MAPPING,
        "writers": PERSON_MAPPING,
        "actors_names": {"type": "text", "analyzer": "ru_en"},
        "writers_names": {"type": "text", "analyzer": "ru_en"},
        # Дата выхода и метка доступа: public — всем, subscription — по подписке.
        "creation_date": {"type": "date"},
        "access_level": {"type": "keyword"},
    },
}

MOVIES_INDEX_BODY = {
    "settings": INDEX_SETTINGS,
    "mappings": MOVIES_MAPPING,
}

GENRES_INDEX = "genres"

GENRES_MAPPING = {
    "dynamic": "strict",
    "properties": {
        "id": {"type": "keyword"},
        "name": {
            "type": "text",
            "analyzer": "ru_en",
            "fields": {"raw": {"type": "keyword"}},
        },
        "description": {"type": "text", "analyzer": "ru_en"},
    },
}

GENRES_INDEX_BODY = {
    "settings": INDEX_SETTINGS,
    "mappings": GENRES_MAPPING,
}

PERSONS_INDEX = "persons"

PERSONS_MAPPING = {
    "dynamic": "strict",
    "properties": {
        "id": {"type": "keyword"},
        "full_name": {
            "type": "text",
            "analyzer": "ru_en",
            "fields": {"raw": {"type": "keyword"}},
        },
        "films": {
            "type": "nested",
            "dynamic": "strict",
            "properties": {
                "id": {"type": "keyword"},
                "roles": {"type": "keyword"},
            },
        },
    },
}

PERSONS_INDEX_BODY = {
    "settings": INDEX_SETTINGS,
    "mappings": PERSONS_MAPPING,
}
