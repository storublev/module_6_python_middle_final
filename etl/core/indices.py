SETTINGS = {
    "refresh_interval": "1s",
    "analysis": {
        "filter": {
            "english_stop": {
                "type": "stop",
                "stopwords": "_english_"
            },
            "english_stemmer": {
                "type": "stemmer",
                "language": "english"
            },
            "english_possessive_stemmer": {
                "type": "stemmer",
                "language": "possessive_english"
            },
            "russian_stop": {
                "type": "stop",
                "stopwords": "_russian_"
            },
            "russian_stemmer": {
                "type": "stemmer",
                "language": "russian"
            }
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
                    "russian_stemmer"
                ]
            }
        }
    }
}

PERSONS_SETTINGS = {
    "type": "nested",
    "dynamic": "strict",
    "properties": {
        "id": {
            "type": "keyword"
        },
        "full_name": {
            "type": "text",
            "analyzer": "ru_en"
        }
    }
}

GENRE_SETTINGS = {
    "id": {
        "type": "keyword"
    },
    "name": {
        "type": "text",
        "analyzer": "ru_en",
        "fields": {
            "raw": {
                "type": "keyword"
            }
        }
    }
}

TEXT_SETTINGS = {
    "type": "text",
    "analyzer": "ru_en"
}

TEXT_KEYWORD_SETTINGS = {
    "type": "text",
    "analyzer": "ru_en",
    "fields": {
        "raw": {
            "type": "keyword"
        }
    }
}

movies_index = {
    "settings": SETTINGS,
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "id": {
                "type": "keyword"
            },
            "imdb_rating": {
                "type": "float"
            },
            "genres": {
                "type": "nested",
                "dynamic": "strict",
                "properties": GENRE_SETTINGS
            },
            "creation_date": {
                "type": "date"
            },
            "title": TEXT_KEYWORD_SETTINGS,
            "description": TEXT_SETTINGS,
            "actors": PERSONS_SETTINGS,
            "writers": PERSONS_SETTINGS,
            "directors": PERSONS_SETTINGS
        }
    }
}

genre_index = {
    "settings": SETTINGS,
    "mappings": {
        "dynamic": "strict",
        "properties": GENRE_SETTINGS
    }
}

person_index = {
    "settings": SETTINGS,
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "id": {
                "type": "keyword"
            },
            "full_name": TEXT_KEYWORD_SETTINGS,
            "role": TEXT_KEYWORD_SETTINGS,
            "film_ids": {
                "type": "keyword"
            }
        }
    }
}
