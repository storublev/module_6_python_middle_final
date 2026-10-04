"""Подтверждение почты: одноразовый токен вместо идентификатора в ссылке."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from models.enums import Channel
from services.assembly import AssemblyService
from services.confirmation import EmailConfirmationService, hash_token
from services.errors import ConfirmationLinkInvalidError
from services.messages import RenderMessage
from services.shortlinks import CONFIRM_PURPOSE
from services.subscriptions import SubscriptionService
from tests.unit.fakes import Database, FakeContactDirectory, FakePublisher, FakeTemplateRepository, recipient, template

REDIRECT = 'https://practix.local/welcome'


def token_of(db: Database, short_url: str) -> str:
    """Достаёт токен из короткой ссылки так, как его увидит обработчик перехода."""
    link = db.links[short_url.rsplit('/', 1)[-1]]
    return parse_qs(urlsplit(link.target_url).query)['token'][0]


async def test_link_carries_token_not_just_user_id(confirmations: EmailConfirmationService, db: Database) -> None:
    """Ссылка несёт токен и адрес возврата, а срок у неё тот же, что у токена.

    Один `user_id` подтверждением служить не может: он не секрет.
    """
    viewer = uuid4()

    url = await confirmations.link_for(viewer, 'neo@example.com', REDIRECT)

    link = db.links[url.rsplit('/', 1)[-1]]
    query = parse_qs(urlsplit(link.target_url).query)
    assert query['redirectUrl'] == [REDIRECT]
    assert 'user_id' not in query
    assert link.purpose == CONFIRM_PURPOSE
    assert link.user_id == viewer
    assert link.expires_at is not None


async def test_token_is_stored_hashed(confirmations: EmailConfirmationService, db: Database) -> None:
    """В хранилище лежит хеш токена, а не он сам: утечка таблицы не даёт готовых ссылок."""
    url = await confirmations.link_for(uuid4(), 'neo@example.com', REDIRECT)

    token = token_of(db, url)
    assert token not in db.confirmation_tokens
    assert hash_token(token) in db.confirmation_tokens


async def test_valid_token_confirms_the_address_it_was_sent_to(
    confirmations: EmailConfirmationService, db: Database,
) -> None:
    """Переход по ссылке подтверждает ровно тот адрес, на который ушло письмо."""
    viewer = uuid4()
    url = await confirmations.link_for(viewer, 'neo@example.com', REDIRECT)

    confirmed = await confirmations.confirm(token_of(db, url))

    assert confirmed.user_id == viewer
    assert confirmed.email == 'neo@example.com'
    status = await confirmations.status(viewer)
    assert status is not None
    assert status.email == 'neo@example.com'


async def test_token_works_only_once(confirmations: EmailConfirmationService, db: Database) -> None:
    """Второй переход по той же ссылке отклоняется: токен одноразовый."""
    url = await confirmations.link_for(uuid4(), 'neo@example.com', REDIRECT)
    token = token_of(db, url)
    await confirmations.confirm(token)

    with pytest.raises(ConfirmationLinkInvalidError):
        await confirmations.confirm(token)


async def test_expired_token_is_rejected(confirmations: EmailConfirmationService, db: Database) -> None:
    """Токен с истёкшим сроком ничего не подтверждает."""
    viewer = uuid4()
    url = await confirmations.link_for(viewer, 'neo@example.com', REDIRECT)

    with pytest.raises(ConfirmationLinkInvalidError):
        await confirmations.confirm(token_of(db, url), now=datetime.now(timezone.utc) + timedelta(days=4))
    assert await confirmations.status(viewer) is None


async def test_made_up_token_is_rejected(confirmations: EmailConfirmationService) -> None:
    """Выдуманный токен отклоняется, а подтверждений не появляется."""
    with pytest.raises(ConfirmationLinkInvalidError):
        await confirmations.confirm('made-up-token')


async def test_knowing_user_id_is_not_enough(confirmations: EmailConfirmationService, db: Database) -> None:
    """Идентификатор зрителя вместо токена ничего не подтверждает.

    Ровно так было до исправления: ссылки с `user_id` хватало, чтобы
    подтвердить чужой адрес.
    """
    victim = uuid4()
    await confirmations.link_for(victim, 'victim@example.com', REDIRECT)

    with pytest.raises(ConfirmationLinkInvalidError):
        await confirmations.confirm(str(victim))
    assert await confirmations.status(victim) is None


async def test_confirmation_does_not_resubscribe(
    confirmations: EmailConfirmationService, subscriptions: SubscriptionService, db: Database,
) -> None:
    """Подтверждение адреса не снимает отказ от рассылок и не заводит подписок.

    Владение ящиком — не согласие на письма. Раньше подтверждение ставилось
    подпиской и заодно возвращало рассылки отписавшемуся зрителю.
    """
    viewer = uuid4()
    await subscriptions.unsubscribe_all(viewer)
    url = await confirmations.link_for(viewer, 'neo@example.com', REDIRECT)

    await confirmations.confirm(token_of(db, url))

    preferences = await subscriptions.list_for_user(viewer)
    assert preferences.unsubscribed_all is True
    assert preferences.items == []


async def test_welcome_letter_gets_personal_confirmation_link(
    assembly: AssemblyService,
    templates_repo: FakeTemplateRepository,
    directory: FakeContactDirectory,
    publisher: FakePublisher,
    db: Database,
) -> None:
    """Сборщик кладёт в письмо ссылку подтверждения с токеном на адрес получателя."""
    welcome = template('welcome', body='<a href="{{ confirm_url }}">Подтвердить</a>')
    templates_repo.db.template_versions[('welcome', 1)] = welcome
    viewer = recipient(email='neo@example.com', timezone='Etc/GMT-12')
    directory.recipients[viewer.user_id] = viewer
    message = RenderMessage(
        event_id=uuid4(), template_code='welcome', template_version=1, channel=Channel.EMAIL,
        user_ids=[viewer.user_id],
    )

    await assembly.assemble(message, now=datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc))

    [sent] = publisher.of('send')
    [(token_hash, token)] = db.confirmation_tokens.items()
    assert token['email'] == 'neo@example.com'
    assert token['user_id'] == viewer.user_id
    short_key = next(key for key, link in db.links.items() if link.purpose == CONFIRM_PURPOSE)
    assert f'/s/{short_key}' in sent['body']


async def test_letter_without_confirmation_link_issues_no_token(
    assembly: AssemblyService,
    templates_repo: FakeTemplateRepository,
    directory: FakeContactDirectory,
    db: Database,
) -> None:
    """Токен не заводится письмам, где ссылки подтверждения нет: подборка базу не засоряет."""
    templates_repo.db.template_versions[('welcome', 1)] = template('welcome')
    viewer = recipient(timezone='Etc/GMT-12')
    directory.recipients[viewer.user_id] = viewer
    message = RenderMessage(
        event_id=uuid4(), template_code='welcome', template_version=1, channel=Channel.EMAIL,
        user_ids=[viewer.user_id],
    )

    await assembly.assemble(message, now=datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc))

    assert db.confirmation_tokens == {}
