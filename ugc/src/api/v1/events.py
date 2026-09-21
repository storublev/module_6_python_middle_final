"""Приём пользовательских событий.

Слой HTTP: разобрать запрос, опознать пользователя, отдать ответ. Бизнес-логика
— в `services/collector.py`, работа с Kafka — в `storage/kafka.py`.
"""

import logging
from dataclasses import asdict
from http import HTTPStatus

from flask import Blueprint, jsonify, request

from api.dependencies import get_services
from api.errors import ApiError
from models.events import EventsRequest
from storage.base import QueueUnavailableError

logger = logging.getLogger(__name__)

router = Blueprint('events', __name__)


@router.post('/events')
def collect_events():
    """Принимает пачку событий: клики, просмотры страниц, кастомные события.

    Возвращает 202: события приняты к обработке, но ещё не доехали до
    аналитического хранилища — обещать это в ответе было бы неправдой, между
    приёмом и хранилищем стоит брокер.

    Каждое событие проверяется отдельно. Если хотя бы одно принято — 202 с
    перечнем отклонённых; если ни одного — 422: повторять такой запрос
    бессмысленно, клиенту нужно чинить данные.
    """
    services = get_services()
    user = services.verifier.verify(request.headers.get('Authorization'))

    # silent=True: свой текст ошибки понятнее, чем страница werkzeug о
    # некорректном JSON.
    body = request.get_json(silent=True)
    if body is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, 'invalid_request', 'Request body must be a JSON object')

    payload = EventsRequest.model_validate(body)
    if len(payload.events) > services.max_events_per_request:
        raise ApiError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            'batch_too_large',
            f'At most {services.max_events_per_request} events are accepted per request',
        )

    try:
        result = services.collector.collect(payload.events, user_id=user.user_id)
    except QueueUnavailableError as error:
        # Клиент не удаляет события из своей очереди и повторит их позже,
        # поэтому отказ честный: «сейчас не принял», а не «принял и потерял».
        logger.warning('Очередь событий недоступна: %s', error)
        raise ApiError(
            HTTPStatus.SERVICE_UNAVAILABLE,
            'queue_unavailable',
            'Event queue is unavailable, retry later',
        ) from error

    response = {'accepted': result.accepted, 'rejected': [asdict(item) for item in result.rejected]}
    if result.accepted == 0:
        return jsonify(response), HTTPStatus.UNPROCESSABLE_ENTITY
    return jsonify(response), HTTPStatus.ACCEPTED
