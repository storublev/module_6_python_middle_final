"""Личный кабинет зрителя: свои уведомления и настройки подписок.

Сюда ходит сам зритель со своим access-токеном. `user_id` берётся из токена, а
не из запроса: иначе любой мог бы отписать любого.
"""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Query

from api.dependencies import Deliveries, SubscriptionServiceDep
from api.errors import TOKEN_ERRORS, error_responses
from api.security import CurrentUser
from api.v1.schemas import (
    DeliveryPageSchema,
    DeliverySchema,
    SubscriptionSchema,
    SubscriptionUpdateSchema,
)

router = APIRouter()

MAX_PAGE_SIZE = 100


@router.get(
    '/notifications',
    response_model=DeliveryPageSchema,
    summary='Мои уведомления',
    description='Последние уведомления зрителя, новые сверху. Здесь видно и то, что доставить не удалось.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def my_notifications(
    user: CurrentUser,
    deliveries: Deliveries,
    page_number: Annotated[int, Query(ge=1, description='Номер страницы, начиная с 1')] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description='Записей на странице')] = 20,
) -> DeliveryPageSchema:
    page = await deliveries.list_for_user(user.user_id, page_number, page_size)
    return DeliveryPageSchema(
        items=[DeliverySchema.model_validate(item) for item in page.items],
        total=page.total,
        page_number=page.page_number,
        page_size=page.page_size,
    )


@router.get(
    '/subscriptions',
    response_model=list[SubscriptionSchema],
    summary='Мои настройки уведомлений',
    description='Явно заданные настройки. Типа, которого здесь нет, зритель ещё не отключал — '
                'по умолчанию уведомления приходят.',
    responses=error_responses(*TOKEN_ERRORS),
)
async def my_subscriptions(
    user: CurrentUser, subscriptions: SubscriptionServiceDep,
) -> list[SubscriptionSchema]:
    return [SubscriptionSchema.model_validate(item) for item in await subscriptions.list_for_user(user.user_id)]


@router.put(
    '/subscriptions',
    response_model=SubscriptionSchema,
    summary='Включить или выключить тип уведомлений',
    responses=error_responses(*TOKEN_ERRORS),
)
async def set_subscription(
    body: SubscriptionUpdateSchema, user: CurrentUser, subscriptions: SubscriptionServiceDep,
) -> SubscriptionSchema:
    updated = await subscriptions.set_enabled(user.user_id, body.template_code, body.channel, body.enabled)
    return SubscriptionSchema.model_validate(updated)


@router.delete(
    '/subscriptions',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Отписаться от всего',
    responses=error_responses(*TOKEN_ERRORS),
)
async def unsubscribe_all(user: CurrentUser, subscriptions: SubscriptionServiceDep) -> None:
    await subscriptions.unsubscribe_all(user.user_id)
