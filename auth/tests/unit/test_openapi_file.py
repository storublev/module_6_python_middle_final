"""Спецификация OpenAPI в docs/openapi.json совпадает с кодом сервиса."""

from pathlib import Path

from export_openapi import render

SPEC_FILE = Path(__file__).parents[2] / 'docs' / 'openapi.json'


def test_openapi_file_is_up_to_date() -> None:
    """Файл спецификации в репозитории соответствует API; обновить: cd auth/src && python export_openapi.py."""
    assert SPEC_FILE.read_text(encoding='utf-8') == render()
