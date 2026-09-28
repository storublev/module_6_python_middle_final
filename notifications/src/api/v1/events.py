"""Приём событий от других сервисов кинотеатра.

Центральный узел системы: **рассылкой этот эндпоинт не занимается**. Он
проверяет контракт, отбивает повтор по `event_id` и кладёт сообщение в
очередь. Всё остальное делают воркеры.

Ходят сюда сервисы, а не пользователи: своего access-токена у них нет,
поэтому опознаются общим секретом в заголовке `X-Service-Token`.
"""

from http import HTTPStatus

from fastapi import APIRouter

from api.dependencies import IngestServiceDep, ServiceToken
from api.errors import error_responses
from api.v1.schemas import AcceptedEventSchema, EventSchema
from models.event import Event
from services.errors import ServiceTokenInvalidError

router = APIRouter()


@router.post(
    '/events',
    response_model=AcceptedEventSchema,
    status_code=HTTPStatus.ACCEPTED,
    summary='Принять событие',
    description=(
        'Принимает событие и кладёт его в очередь. Ответ приходит сразу: сбор данных, сборка письма и '
        'отправка происходят в воркерах, а отправитель события их не ждёт.\n\n'
        '`event_id` задаёт отправитель — это ключ идемпотентности. Повтор запроса после потерянного ответа '
        'вернёт `accepted: false` и второго письма не создаст.'
    ),
    responses=error_responses(ServiceTokenInvalidError),
)
async def accept_event(
    body: EventSchema, ingest: IngestServiceDep, _: ServiceToken = None,
) -> AcceptedEventSchema:
    accepted = await ingest.accept(Event.model_validate(body.model_dump()))
    return AcceptedEventSchema.model_validate(accepted)
