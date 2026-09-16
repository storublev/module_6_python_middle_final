"""Вход через соцсеть: state, опознание пользователя, привязка и открепление."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from models.social import SocialProfile
from services.auth import Principal
from services.errors import (
    InvalidCredentialsError,
    LastLoginMethodError,
    OAuthStateInvalidError,
    ProviderNotFoundError,
    SocialAccountTakenError,
    SocialLinkExpiredError,
)
from services.tokens import TokenType
from tests.unit.conftest import CLIENT, PASSWORD
from tests.unit.fakes import REJECTED, UNAVAILABLE

REDIRECT_URI = 'http://localhost/auth/api/v1/oauth/yandex/callback'
CODE = 'one-time-code'
NEO = SocialProfile(social_id='42', display_name='Neo', email='neo@example.com')
TRINITY = SocialProfile(social_id='43', display_name='Trinity', email='trinity@example.com')


@pytest.fixture(autouse=True)
def neo(provider):
    """По умолчанию поставщик отвечает профилем Нео."""
    provider.profile = NEO


def principal_of(result, tokens) -> Principal:
    """Пользователь и сессия из токенов, которые выдал вход."""
    claims = tokens.decode(result.tokens.access_token, TokenType.ACCESS)
    return Principal(user_id=claims.user_id, session_id=claims.session_id)


async def start(social, provider_name='yandex', link_to=None) -> str:
    """Начинает вход и возвращает state из ссылки на поставщика."""
    url = await social.start(provider_name, redirect_uri=REDIRECT_URI, link_to=link_to)
    return parse_qs(urlparse(url).query)['state'][0]


async def sign_in(auth, tokens, login='neo') -> Principal:
    """Входит по паролю и возвращает пользователя и сессию выданного токена."""
    pair = await auth.login(login, PASSWORD, CLIENT)
    claims = tokens.decode(pair.access_token, TokenType.ACCESS)
    return Principal(user_id=claims.user_id, session_id=claims.session_id)


async def signed_in(registration, auth, tokens, login='neo') -> Principal:
    """Регистрирует пользователя и входит: привязку начинают из действующей сессии."""
    await registration.register(login, PASSWORD)
    return await sign_in(auth, tokens, login)


# Начало входа

async def test_start_leads_to_provider(social):
    """Вход начинается переходом на страницу поставщика."""
    url = await social.start('yandex', redirect_uri=REDIRECT_URI)

    assert url.startswith('https://yandex.example.com/authorize')


async def test_start_remembers_state(social, oauth_states):
    """Начатый вход запоминается: по возврату мы узнаем, что он наш."""
    state = await start(social)

    assert state in oauth_states.states


async def test_unknown_provider_is_rejected(social):
    """Через ненастроенного поставщика войти нельзя."""
    with pytest.raises(ProviderNotFoundError):
        await social.start('facebook', redirect_uri=REDIRECT_URI)


# Возврат от поставщика

async def test_first_login_creates_user(social, db):
    """Первый вход через соцсеть заводит учётную запись."""
    state = await start(social)

    result = await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert result.created
    assert len(db.users) == 1


async def test_first_login_returns_tokens(social):
    """Вход через соцсеть выдаёт такую же пару токенов, как вход по паролю."""
    state = await start(social)

    result = await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert result.tokens is not None
    assert result.tokens.access_token


async def test_new_user_has_no_password(social, db):
    """Пользователь из соцсети заводится без пароля: войти по паролю в него нельзя."""
    state = await start(social)

    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert not next(iter(db.users.values())).has_password


async def test_user_without_password_cannot_log_in_with_one(social, auth, db):
    """Вход по паролю в учётную запись из соцсети не проходит, пароль там подобрать нечем."""
    state = await start(social)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)
    login = next(iter(db.users.values())).login

    with pytest.raises(InvalidCredentialsError):
        await auth.login(login, PASSWORD, CLIENT)


async def test_second_login_reuses_user(social, db):
    """Повторный вход тем же аккаунтом соцсети не плодит учётные записи."""
    for _ in range(2):
        state = await start(social)
        result = await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert not result.created
    assert len(db.users) == 1


async def test_different_accounts_get_different_users(social, provider, db):
    """Разные аккаунты соцсети — разные пользователи: опознаём по social_id."""
    state = await start(social)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)
    provider.profile = TRINITY
    state = await start(social)

    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert len(db.users) == 2


async def test_same_email_does_not_merge_accounts(social, provider, db):
    """Совпадение email не связывает аккаунты: email в соцсети меняют и не всегда подтверждают."""
    state = await start(social)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)
    provider.profile = SocialProfile(social_id='99', display_name='Someone else', email=NEO.email)
    state = await start(social)

    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert len(db.users) == 2


async def test_login_is_recorded_in_history(social, db):
    """Вход через соцсеть попадает в историю входов наравне с обычным."""
    state = await start(social)

    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert len(db.history) == 1


# state

async def test_unknown_state_is_rejected(social):
    """Возврат с чужим state отвергается: так чужой ответ поставщика не привяжется к нам."""
    with pytest.raises(OAuthStateInvalidError):
        await social.complete('yandex', code=CODE, state='not-ours', redirect_uri=REDIRECT_URI, client=CLIENT)


async def test_missing_state_is_rejected(social):
    """Возврат без state отвергается."""
    with pytest.raises(OAuthStateInvalidError):
        await social.complete('yandex', code=CODE, state='', redirect_uri=REDIRECT_URI, client=CLIENT)


async def test_state_works_only_once(social):
    """state одноразовый: повторный возврат с тем же значением не сработает."""
    state = await start(social)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    with pytest.raises(OAuthStateInvalidError):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)


async def test_state_of_another_provider_is_rejected(social, oauth_states):
    """state, выданный для другого поставщика, не принимается: ответ пришёл не туда."""
    state = await start(social)
    oauth_states.states[state] = json.dumps({'provider': 'google', 'link_to': None})

    with pytest.raises(OAuthStateInvalidError):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)


async def test_discarded_state_does_not_work(social):
    """Брошенный вход гасится: его state не сработает позже."""
    state = await start(social)

    await social.discard(state, 'yandex')

    with pytest.raises(OAuthStateInvalidError):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)


async def test_provider_receives_the_code(social, provider):
    """Код из возврата уходит поставщику на обмен."""
    state = await start(social)

    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert provider.codes == [CODE]


# Привязка к существующей учётной записи

async def test_link_attaches_account_to_current_user(social, registration, auth, tokens):
    """Вход, начатый с токеном, привязывает аккаунт, а не заводит нового пользователя."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)

    result = await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert result.tokens is None
    assert result.account.user_id == principal.user_id


async def test_linked_account_logs_into_the_same_user(social, registration, auth, tokens):
    """После привязки вход через соцсеть ведёт в ту же учётную запись."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)
    state = await start(social)

    result = await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert not result.created


async def test_account_cannot_be_linked_twice(social, registration, auth, tokens):
    """Аккаунт соцсети нельзя привязать ко второй учётной записи."""
    first = await signed_in(registration, auth, tokens)
    second = await signed_in(registration, auth, tokens, login='trinity')
    state = await start(social, link_to=first)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)
    state = await start(social, link_to=second)

    with pytest.raises(SocialAccountTakenError):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)


async def test_link_target_is_decided_at_start(social, registration, auth, tokens, oauth_states):
    """К кому привязывать, решается в начале входа, а не на возврате.

    Иначе чужой ответ поставщика мог бы привязаться к учётной записи того, кто
    в этот момент просто вошёл на сайт.
    """
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)

    assert json.loads(oauth_states.states[state])['link_to']['user_id'] == str(principal.user_id)


async def test_link_remembers_the_session_it_started_from(social, registration, auth, tokens, oauth_states):
    """Вместе со state запоминается сессия и версия учётных данных: по ним привязка сверяется."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)

    link = json.loads(oauth_states.states[state])['link_to']

    assert link['session_id'] == str(principal.session_id)
    assert link['credentials_version'] == 0


async def test_link_is_cancelled_by_password_change(social, registration, auth, tokens, profiles):
    """Смена пароля отменяет начатую привязку: украденным токеном её не закончить."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)
    await profiles.change_password(principal, PASSWORD, 'newpassword123')

    with pytest.raises(SocialLinkExpiredError):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)


async def test_cancelled_link_leaves_no_account(social, registration, auth, tokens, profiles, db):
    """Отменённая привязка не оставляет за собой связанного аккаунта."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)
    await profiles.change_password(principal, PASSWORD, 'newpassword123')
    with pytest.raises(SocialLinkExpiredError):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert db.social_accounts == {}


async def test_link_is_cancelled_by_logout(social, registration, auth, tokens):
    """Выход отменяет начатую привязку: сессии, из которой её начали, больше нет."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)
    await auth.logout(principal)

    with pytest.raises(SocialLinkExpiredError):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)


async def test_link_is_cancelled_by_logout_from_other_sessions(social, registration, auth, tokens):
    """«Выйти из остальных устройств» отменяет привязку, начатую в одной из закрытых сессий."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)
    await auth.logout_others(await sign_in(auth, tokens))

    with pytest.raises(SocialLinkExpiredError):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)


# Личный кабинет

async def test_linked_accounts_are_listed(social, registration, auth, tokens):
    """Привязанные аккаунты видны в личном кабинете."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    accounts = await social.list_accounts(principal.user_id)

    assert [(account.provider, account.social_id) for account in accounts] == [('yandex', NEO.social_id)]


async def test_account_can_be_unlinked(social, registration, auth, tokens):
    """Аккаунт с паролем открепляется: способ войти у пользователя остаётся."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert await social.unlink(principal.user_id, 'yandex')
    assert await social.list_accounts(principal.user_id) == []


async def test_unlinking_missing_account_is_reported(social, registration):
    """Открепление непривязанного аккаунта — не ошибка сервера, а «такого нет»."""
    user = await registration.register('neo', PASSWORD)

    assert not await social.unlink(user.id, 'yandex')


async def test_last_login_method_cannot_be_unlinked(social, db):
    """Единственный способ войти открепить нельзя: пользователь потерял бы доступ."""
    state = await start(social)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)
    user_id = next(iter(db.users))

    with pytest.raises(LastLoginMethodError):
        await social.unlink(user_id, 'yandex')


async def test_unlink_is_allowed_after_password_is_set(social, profiles, tokens):
    """Задав пароль, пользователь из соцсети может открепить аккаунт: способ войти остаётся."""
    state = await start(social)
    result = await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)
    principal = principal_of(result, tokens)
    await profiles.change_password(principal, None, 'newpassword123')

    assert await social.unlink(principal.user_id, 'yandex')


async def test_unlink_is_allowed_with_another_account_linked(social, registration, auth, tokens):
    """Пока есть пароль, открепить соцсеть можно: она не единственный способ войти."""
    principal = await signed_in(registration, auth, tokens)
    state = await start(social, link_to=principal)
    await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert await social.unlink(principal.user_id, 'yandex')


# Сбои поставщика

@pytest.mark.parametrize('error', [REJECTED, UNAVAILABLE], ids=['rejected', 'unavailable'])
async def test_provider_failure_reaches_the_caller(social, provider, error):
    """Отказ и недоступность поставщика доходят до вызывающего кода, а не молча создают пользователя."""
    provider.error = error
    state = await start(social)

    with pytest.raises(type(error)):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)


async def test_failed_login_creates_no_user(social, provider, db):
    """Неудачный обмен кода не оставляет за собой пустых учётных записей."""
    provider.error = REJECTED
    state = await start(social)
    with pytest.raises(type(REJECTED)):
        await social.complete('yandex', code=CODE, state=state, redirect_uri=REDIRECT_URI, client=CLIENT)

    assert db.users == {}
