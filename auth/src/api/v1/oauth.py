"""Вход через соцсети: сторона потребителя OAuth 2.0.

Своего сервера OAuth и фронтенда здесь нет. Пользователь открывает
`/oauth/{provider}/login`, сервис перенаправляет его к поставщику, а тот
возвращает его на `/oauth/{provider}/callback` с одноразовым кодом. Код
меняется на токены на стороне сервиса, где лежит client_secret.

Возврат от поставщика — обычный переход браузера, поэтому это GET, и
привязать его к пользователю заголовком Authorization нельзя: браузер его не
отправит. Кто начал вход, сервис помнит по `state` — он же защищает от чужого
ответа поставщика.
"""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from api.dependencies import SocialAuthServiceDep
from api.errors import error_responses
from api.security import OptionalPrincipalDep
from api.v1.auth import client_info
from api.v1.schemas import ProviderSchema, SocialLoginSchema
from core.config import settings
from services.errors import (
    OAuthProviderUnavailableError,
    OAuthRejectedError,
    OAuthStateInvalidError,
    ProviderNotFoundError,
    SocialAccountTakenError,
    SocialLinkExpiredError,
    TokenExpiredError,
    TokenInvalidError,
    TokenRevokedError,
)
from storage.base import ProviderRejectedError, ProviderUnavailableError

router = APIRouter()

CALLBACK_PATH = '/auth/api/v1/oauth/{provider}/callback'


def callback_url(provider: str) -> str:
    """Адрес возврата — тот же, что зарегистрирован у поставщика.

    Собирается из настройки, а не из адреса запроса: поставщик сверяет
    redirect_uri посимвольно и с подменённым заголовком Host не совпадёт.
    """
    return settings.oauth_redirect_base_url.rstrip('/') + CALLBACK_PATH.format(provider=provider)


@router.get(
    '/oauth/providers',
    response_model=list[ProviderSchema],
    summary='Доступные соцсети',
    description='Поставщики, через которых можно войти. Список зависит от настроек: '
                'поставщик без ключей приложения в него не попадает.',
)
async def providers(social: SocialAuthServiceDep) -> list[ProviderSchema]:
    return [ProviderSchema(name=provider.name, title=provider.title) for provider in social.providers.values()]


@router.get(
    '/oauth/{provider}/login',
    status_code=HTTPStatus.TEMPORARY_REDIRECT,
    summary='Вход через соцсеть',
    description='Перенаправляет к поставщику. После согласия пользователя тот вернёт его на callback, '
                'и сервис выдаст пару токенов. С access-токеном в заголовке вместо входа выполняется '
                'привязка аккаунта соцсети к текущему пользователю: завершить её может только тот же '
                'вход, пока он действует.',
    responses={
        HTTPStatus.TEMPORARY_REDIRECT: {'description': 'Переход на страницу входа поставщика'},
        # Токен здесь необязателен, поэтому not_authenticated быть не может,
        # а вот непригодный токен — вполне.
        **error_responses(ProviderNotFoundError, TokenExpiredError, TokenInvalidError, TokenRevokedError),
    },
)
async def oauth_login(
    provider: str,
    principal: OptionalPrincipalDep,
    social: SocialAuthServiceDep,
) -> RedirectResponse:
    url = await social.start(provider, redirect_uri=callback_url(provider), link_to=principal)
    return RedirectResponse(url, status_code=HTTPStatus.TEMPORARY_REDIRECT)


@router.get(
    '/oauth/{provider}/callback',
    response_model=SocialLoginSchema,
    summary='Возврат от соцсети',
    description='Меняет код поставщика на пару токенов. Если вход начинали с access-токеном, '
                'аккаунт привязывается к текущему пользователю, и токены не выдаются; привязка '
                'не состоится, если к этому времени тот вход закончился — сессию закрыли или '
                'сменили пароль. Первый вход через соцсеть заводит учётную запись без пароля: '
                'задать его можно в личном кабинете.',
    # Токен на возврате не читается: это переход браузера от поставщика.
    responses=error_responses(
        OAuthStateInvalidError,
        OAuthRejectedError,
        SocialLinkExpiredError,
        SocialAccountTakenError,
        ProviderNotFoundError,
        OAuthProviderUnavailableError,
    ),
)
async def oauth_callback(
    provider: str,
    request: Request,
    social: SocialAuthServiceDep,
    code: Annotated[str, Query(description='Одноразовый код от поставщика')] = '',
    state: Annotated[str, Query(description='Значение, с которым начинали вход')] = '',
    error: Annotated[str | None, Query(description='Причина отказа, если поставщик его вернул')] = None,
) -> SocialLoginSchema:
    if error or not code:
        # Пользователь отказался или поставщик сообщил об ошибке: state всё
        # равно гасим, чтобы брошенный вход не ждал своего часа.
        await social.discard(state, provider)
        raise OAuthRejectedError(f'Social provider returned an error: {error}' if error else None)
    try:
        result = await social.complete(
            provider, code=code, state=state, redirect_uri=callback_url(provider), client=client_info(request),
        )
    except ProviderRejectedError as exc:
        raise OAuthRejectedError from exc
    except ProviderUnavailableError as exc:
        # Сбой поставщика — не отказ во входе: повторить стоит позже.
        raise OAuthProviderUnavailableError from exc
    return SocialLoginSchema.model_validate(result)
