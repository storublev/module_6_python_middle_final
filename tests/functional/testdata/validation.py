"""Граничные случаи параметров, общие для всех списков API."""

import pytest

# Произведение page_number * page_size ограничено окном выдачи Elasticsearch.
MAX_RESULT_WINDOW = 10_000
MAX_PAGE_SIZE = 100

INVALID_PAGINATION = [
    pytest.param({'page_size': 0}, id='page_size=0'),
    pytest.param({'page_size': -1}, id='page_size<0'),
    pytest.param({'page_size': MAX_PAGE_SIZE + 1}, id='page_size>max'),
    pytest.param({'page_size': 'ten'}, id='page_size-not-int'),
    pytest.param({'page_number': 0}, id='page_number=0'),
    pytest.param({'page_number': -1}, id='page_number<0'),
    pytest.param({'page_number': 'one'}, id='page_number-not-int'),
    pytest.param({'page_number': MAX_RESULT_WINDOW // MAX_PAGE_SIZE + 1, 'page_size': MAX_PAGE_SIZE},
                 id='beyond-result-window'),
]

# Крайние допустимые значения: запрос проходит валидацию.
VALID_PAGINATION_EDGES = [
    pytest.param({'page_size': 1}, id='page_size=1'),
    pytest.param({'page_size': MAX_PAGE_SIZE}, id='page_size=max'),
    pytest.param({'page_number': MAX_RESULT_WINDOW // MAX_PAGE_SIZE, 'page_size': MAX_PAGE_SIZE},
                 id='last-page-in-result-window'),
]

INVALID_UUIDS = [
    pytest.param('not-a-uuid', id='text'),
    pytest.param('123', id='number'),
    pytest.param('724e5631-e14b-14e3-g556-1b84b31b2f71', id='non-hex-digit'),
]
