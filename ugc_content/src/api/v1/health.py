"""Проверки живости и готовности для оркестратора.

Живость отвечает, пока жив процесс: перезапускать сервис из-за недоступной
MongoDB бессмысленно — он поднимется в то же состояние. Готовность проверяет
хранилище: пока оно молчит, трафик на этот экземпляр слать незачем.
"""

from http import HTTPStatus

from fastapi import APIRouter, Response
from pydantic import BaseModel

from api.dependencies import Health

router = APIRouter(tags=['Служебное'])


class Status(BaseModel):
    """Состояние сервиса."""

    status: str


@router.get('/health', response_model=Status, summary='Жив ли сервис')
async def health() -> Status:
    return Status(status='ok')


@router.get(
    '/ready',
    response_model=Status,
    summary='Готов ли сервис принимать запросы',
    # Ключ — число: FastAPI переводит его в строку через str(), а у HTTPStatus
    # это поведение менялось между версиями Python (см. api/errors.py).
    responses={int(HTTPStatus.SERVICE_UNAVAILABLE): {'model': Status, 'description': 'MongoDB недоступна'}},
)
async def ready(service: Health, response: Response) -> Status:
    if await service.is_ready():
        return Status(status='ready')
    response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
    return Status(status='not ready')
