"""Схемы индексов Elasticsearch — такие же, как у ETL.

Копия `etl/core/schemas.py` из репозитория new_admin_panel_sprint_3.
Функциональные тесты не импортируют код других сервисов и самого API:
схема здесь — часть контракта, который проверяют тесты.
"""

MOVIES_INDEX = 'movies'
GENRES_INDEX = 'genres'
PERSONS_INDEX = 'persons'

INDEX_SETTINGS = {
    'refresh_interval': '1s',
    'analysis': {
        'filter': {
            'english_stop': {'type': 'stop', 'stopwords': '_english_'},
            'english_stemmer': {'type': 'stemmer', 'language': 'english'},
            'english_possessive_stemmer': {'type': 'stemmer', 'language': 'possessive_english'},
            'russian_stop': {'type': 'stop', 'stopwords': '_russian_'},
            'russian_stemmer': {'type': 'stemmer', 'language': 'russian'},
        },
        'analyzer': {
            'ru_en': {
                'tokenizer': 'standard',
                'filter': [
                    'lowercase',
                    'english_stop',
                    'english_stemmer',
                    'english_possessive_stemmer',
                    'russian_stop',
                    'russian_stemmer',
                ],
            },
        },
    },
}

NESTED_REF_MAPPING = {
    'type': 'nested',
    'dynamic': 'strict',
    'properties': {
        'id': {'type': 'keyword'},
        'name': {'type': 'text', 'analyzer': 'ru_en'},
    },
}

MOVIES_MAPPING = {
    'dynamic': 'strict',
    'properties': {
        'id': {'type': 'keyword'},
        'imdb_rating': {'type': 'float'},
        'genres': NESTED_REF_MAPPING,
        'title': {'type': 'text', 'analyzer': 'ru_en', 'fields': {'raw': {'type': 'keyword'}}},
        'description': {'type': 'text', 'analyzer': 'ru_en'},
        'directors': NESTED_REF_MAPPING,
        'actors': NESTED_REF_MAPPING,
        'writers': NESTED_REF_MAPPING,
        'actors_names': {'type': 'text', 'analyzer': 'ru_en'},
        'writers_names': {'type': 'text', 'analyzer': 'ru_en'},
        # Дата выхода и метка доступа для сервиса авторизации: public — всем,
        # subscription — только с правом films.subscription.
        'creation_date': {'type': 'date'},
        'access_level': {'type': 'keyword'},
    },
}

GENRES_MAPPING = {
    'dynamic': 'strict',
    'properties': {
        'id': {'type': 'keyword'},
        'name': {'type': 'text', 'analyzer': 'ru_en', 'fields': {'raw': {'type': 'keyword'}}},
        'description': {'type': 'text', 'analyzer': 'ru_en'},
    },
}

PERSONS_MAPPING = {
    'dynamic': 'strict',
    'properties': {
        'id': {'type': 'keyword'},
        'full_name': {'type': 'text', 'analyzer': 'ru_en', 'fields': {'raw': {'type': 'keyword'}}},
        'films': {
            'type': 'nested',
            'dynamic': 'strict',
            'properties': {
                'id': {'type': 'keyword'},
                'roles': {'type': 'keyword'},
            },
        },
    },
}

# Тело запроса на создание индекса: indices.create(index=..., **INDICES[index]).
INDICES = {
    MOVIES_INDEX: {'settings': INDEX_SETTINGS, 'mappings': MOVIES_MAPPING},
    GENRES_INDEX: {'settings': INDEX_SETTINGS, 'mappings': GENRES_MAPPING},
    PERSONS_INDEX: {'settings': INDEX_SETTINGS, 'mappings': PERSONS_MAPPING},
}
