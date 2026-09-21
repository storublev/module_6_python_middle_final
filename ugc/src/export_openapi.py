"""Выгружает спецификацию OpenAPI сервиса в файл: python export_openapi.py ../docs/openapi.json

Спецификация строится по коду и не требует ни Kafka, ни запущенного сервиса.
Файл в репозитории нужен тем, кто читает API без запущенного сервиса: его можно
открыть в editor.swagger.io. Актуальность файла проверяет unit-тест.
"""

import json
import sys
from pathlib import Path

from api.openapi import build_spec


def render() -> str:
    return json.dumps(build_spec(), ensure_ascii=False, indent=2) + '\n'


if __name__ == '__main__':
    target = Path(sys.argv[1] if len(sys.argv) > 1 else '../docs/openapi.json')
    target.write_text(render(), encoding='utf-8')
    print(f'Спецификация записана в {target}')
