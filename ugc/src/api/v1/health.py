"""Проверки живости и готовности.

Живость (`/health`) отвечает, пока жив процесс: по ней оркестратор решает,
не пора ли перезапустить контейнер. Готовность (`/ready`) учитывает ещё и
соединение с брокером: пока его нет, принимать события бессмысленно, и
балансировщик не должен слать сюда запросы.

Разделены они потому, что недоступность Kafka — не повод перезапускать
сервис: он поднимется таким же, а соединение восстановится само.
"""

from http import HTTPStatus

from flask import Blueprint, jsonify

from api.dependencies import get_services

router = Blueprint('health', __name__)


@router.get('/health')
def health():
    """Жив ли процесс."""
    return jsonify({'status': 'ok'}), HTTPStatus.OK


@router.get('/ready')
def ready():
    """Готов ли сервис принимать события: есть ли соединение с брокером."""
    if get_services().queue.is_ready():
        return jsonify({'status': 'ok'}), HTTPStatus.OK
    return jsonify({'status': 'unavailable', 'detail': 'event queue is not connected'}), HTTPStatus.SERVICE_UNAVAILABLE
