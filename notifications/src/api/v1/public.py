"""Эндпоинты, доступные из письма, где зритель не залогинен.

Отписка подтверждается не сессией, а подписью в ссылке, подтверждение
адреса — одноразовым токеном: отписать чужого или подтвердить чужой адрес,
зная только идентификатор зрителя, нельзя.
"""

from datetime import timedelta
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from api.dependencies import ConfirmationServiceDep, ServiceToken, ShortLinkServiceDep, SubscriptionServiceDep
from api.errors import error_responses
from api.v1.schemas import HealthSchema, ShortenSchema, ShortLinkSchema
from services.confirmation import TOKEN_BYTES
from services.errors import (
    ConfirmationLinkInvalidError,
    LinkNotFoundError,
    ServiceTokenInvalidError,
    TokenInvalidError,
)

router = APIRouter()


@router.get(
    '/unsubscribe',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Отписаться по ссылке из письма',
    description='Работает без входа в аккаунт: человек, который хочет отписаться, не должен вспоминать пароль. '
                'Подлинность ссылки подтверждает подпись в параметре `token`.',
    responses=error_responses(TokenInvalidError),
)
async def unsubscribe(
    subscriptions: SubscriptionServiceDep,
    user_id: Annotated[UUID, Query(description='Кого отписать')],
    token: Annotated[str, Query(description='Подпись из ссылки в письме')],
) -> None:
    await subscriptions.unsubscribe_by_token(user_id, token)


@router.get(
    '/confirm-email',
    status_code=HTTPStatus.FOUND,
    summary='Подтвердить адрес почты',
    description='Сюда ведёт короткая ссылка из приветственного письма. Одноразовый токен из ссылки '
                'подтверждает, что письмо дошло до владельца ящика: адрес помечается подтверждённым, и '
                'зритель уходит на `redirectUrl`.\n\n'
                'Токен привязан к зрителю и адресу, действует ограниченное время и гасится при первом '
                'переходе. Подписки и отказ от рассылок подтверждение не меняет.',
    response_class=RedirectResponse,
    responses=error_responses(ConfirmationLinkInvalidError),
)
async def confirm_email(
    confirmations: ConfirmationServiceDep,
    token: Annotated[
        # Предел длины — с запасом над длиной настоящего токена: base64 от 32
        # байт занимает 43 символа, а длинная строка в запросе к базе незачем.
        str, Query(min_length=1, max_length=TOKEN_BYTES * 4, description='Одноразовый токен из письма'),
    ],
    redirect_url: Annotated[
        str, Query(alias='redirectUrl', description='Куда вести после подтверждения'),
    ],
) -> RedirectResponse:
    await confirmations.confirm(token)
    return RedirectResponse(redirect_url, status_code=HTTPStatus.FOUND)


@router.post(
    '/links',
    response_model=ShortLinkSchema,
    status_code=HTTPStatus.CREATED,
    summary='Сократить ссылку',
    description='Заводит короткую ссылку. Нужна воркеру при сборке письма и админ-панели при подготовке рассылки.',
    responses=error_responses(ServiceTokenInvalidError),
)
async def shorten(
    body: ShortenSchema, links: ShortLinkServiceDep, _: ServiceToken = None,
) -> ShortLinkSchema:
    ttl = timedelta(seconds=body.ttl_seconds) if body.ttl_seconds else None
    link = await links.shorten(body.target_url, user_id=body.user_id, ttl=ttl, purpose=body.purpose)
    return ShortLinkSchema(
        key=link.key, url=links.url_of(link.key), target_url=link.target_url, expires_at=link.expires_at,
    )


@router.get(
    '/health',
    response_model=HealthSchema,
    summary='Проверка готовности',
    description='Отвечает 200, когда сервис готов принимать запросы. Используется проверкой контейнера.',
)
async def health() -> HealthSchema:
    return HealthSchema(status='ok')


redirect_router = APIRouter()


@redirect_router.get(
    '/{key}',
    status_code=HTTPStatus.FOUND,
    summary='Переход по короткой ссылке',
    description='Ведёт на исходный адрес кодом 302 и считает переход. Просроченная ссылка отдаёт 404 — '
                'так требует задание урока «Короткие ссылки».\n\n'
                'Код 302, а не 301: постоянное перенаправление браузер кеширует навсегда и после окончания '
                'срока действия всё равно поведёт по старому адресу, не спросив нас.',
    responses=error_responses(LinkNotFoundError),
    response_class=RedirectResponse,
)
async def follow(key: str, links: ShortLinkServiceDep) -> RedirectResponse:
    link = await links.resolve(key)
    return RedirectResponse(link.target_url, status_code=HTTPStatus.FOUND)
