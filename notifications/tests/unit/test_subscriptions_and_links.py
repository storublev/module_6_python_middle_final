"""Настройки уведомлений, отписка по ссылке и короткие ссылки."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from models.enums import Channel
from services.errors import LinkNotFoundError, TokenInvalidError
from services.shortlinks import CONFIRM_PURPOSE, ShortLinkService
from services.subscriptions import SubscriptionService
from tests.unit.fakes import Database


async def test_disabled_type_is_remembered(subscriptions: SubscriptionService) -> None:
    """Выключенный тип уведомлений сохраняется в настройках зрителя."""
    viewer = uuid4()

    await subscriptions.set_enabled(viewer, 'weekly_digest', Channel.EMAIL, enabled=False)

    saved = await subscriptions.list_for_user(viewer)
    assert [(item.template_code, item.enabled) for item in saved.items] == [('weekly_digest', False)]


async def test_unsubscribe_all_turns_everything_off(subscriptions: SubscriptionService) -> None:
    """Отписка от всего выключает все явные настройки зрителя."""
    viewer = uuid4()
    await subscriptions.set_enabled(viewer, 'weekly_digest', Channel.EMAIL, enabled=True)
    await subscriptions.set_enabled(viewer, 'new_episode', Channel.EMAIL, enabled=True)

    await subscriptions.unsubscribe_all(viewer)

    saved = await subscriptions.list_for_user(viewer)
    assert saved.unsubscribed_all is True
    assert all(not item.enabled for item in saved.items)


async def test_unsubscribe_works_for_viewer_without_any_settings(
    subscriptions: SubscriptionService, subscriptions_repo,
) -> None:
    """Отписка работает и у зрителя, который ничего не настраивал.

    Это главный случай: настройки есть у единиц, а отписывается из письма кто
    угодно. Если «отписать от всего» означает только «выключить заданное», то
    у такого зрителя выключать нечего — и письма продолжают приходить.
    """
    viewer = uuid4()

    await subscriptions.unsubscribe_all(viewer)

    allowed = await subscriptions_repo.filter_enabled([viewer], 'weekly_digest', Channel.EMAIL)
    assert allowed == set()


async def test_enabling_a_type_brings_the_viewer_back(
    subscriptions: SubscriptionService, subscriptions_repo,
) -> None:
    """Включение любого типа снимает общий отказ: вернуться должно быть можно."""
    viewer = uuid4()
    await subscriptions.unsubscribe_all(viewer)

    await subscriptions.set_enabled(viewer, 'weekly_digest', Channel.EMAIL, enabled=True)

    allowed = await subscriptions_repo.filter_enabled([viewer], 'weekly_digest', Channel.EMAIL)
    assert allowed == {viewer}


async def test_unsubscribe_link_works_without_login(subscriptions: SubscriptionService) -> None:
    """Отписаться можно по подписи из письма, не вспоминая пароль (ФТ-11)."""
    viewer = uuid4()
    await subscriptions.set_enabled(viewer, 'weekly_digest', Channel.EMAIL, enabled=True)

    await subscriptions.unsubscribe_by_token(viewer, subscriptions.unsubscribe_token(viewer))

    saved = await subscriptions.list_for_user(viewer)
    assert saved.unsubscribed_all is True


async def test_wrong_signature_cannot_unsubscribe_others(subscriptions: SubscriptionService) -> None:
    """Чужую подпись подставить нельзя: отписать другого по угаданной ссылке невозможно."""
    victim, attacker = uuid4(), uuid4()
    await subscriptions.set_enabled(victim, 'weekly_digest', Channel.EMAIL, enabled=True)

    with pytest.raises(TokenInvalidError):
        await subscriptions.unsubscribe_by_token(victim, subscriptions.unsubscribe_token(attacker))

    saved = await subscriptions.list_for_user(victim)
    assert saved.unsubscribed_all is False
    assert all(item.enabled for item in saved.items)


async def test_confirmation_link_carries_user_and_redirect(shortlinks: ShortLinkService, db: Database) -> None:
    """Ссылка подтверждения несёт идентификатор зрителя и адрес возврата.

    Оба перечисляет задание урока «Короткие ссылки»: по идентификатору
    считаются визиты, по `redirectUrl` — куда вести после подтверждения.
    """
    viewer = uuid4()

    url = await shortlinks.confirmation_link(viewer, redirect_url='https://practix.local/')

    key = url.rsplit('/', 1)[-1]
    link = db.links[key]
    assert str(viewer) in link.target_url
    assert 'redirectUrl' in link.target_url
    assert link.purpose == CONFIRM_PURPOSE


async def test_confirmation_link_expires(shortlinks: ShortLinkService, db: Database) -> None:
    """У ссылки подтверждения есть срок жизни."""
    url = await shortlinks.confirmation_link(uuid4(), redirect_url='https://practix.local/')

    key = url.rsplit('/', 1)[-1]
    assert db.links[key].expires_at is not None


async def test_expired_link_is_not_found(shortlinks: ShortLinkService) -> None:
    """Просроченная ссылка отдаёт 404, а не ведёт по старому адресу.

    Так требует задание урока: «при переходе по просроченной ссылке должна
    открываться страница с ошибкой 404».
    """
    link = await shortlinks.shorten('https://practix.local/', ttl=timedelta(seconds=1))

    with pytest.raises(LinkNotFoundError):
        await shortlinks.resolve(link.key, now=datetime.now(timezone.utc) + timedelta(minutes=1))


async def test_unknown_key_is_not_found(shortlinks: ShortLinkService) -> None:
    """Несуществующий ключ отдаёт 404."""
    with pytest.raises(LinkNotFoundError):
        await shortlinks.resolve('nosuchkey')


async def test_visit_is_counted(shortlinks: ShortLinkService, db: Database) -> None:
    """Каждый переход считается: ради этого в ссылке и лежит идентификатор зрителя."""
    link = await shortlinks.shorten('https://practix.local/', user_id=uuid4())

    await shortlinks.resolve(link.key)
    await shortlinks.resolve(link.key)

    assert db.links[link.key].visits == 2


async def test_link_without_ttl_lives_forever(shortlinks: ShortLinkService) -> None:
    """Ссылка без срока жизни работает и через год: не у всех ссылок он нужен."""
    link = await shortlinks.shorten('https://practix.local/')

    resolved = await shortlinks.resolve(link.key, now=datetime.now(timezone.utc) + timedelta(days=365))

    assert resolved.target_url == 'https://practix.local/'


async def test_email_confirmation_is_recorded(subscriptions: SubscriptionService) -> None:
    """Переход по ссылке подтверждения отмечает адрес подтверждённым."""
    viewer = uuid4()

    await subscriptions.confirm_email(viewer)

    saved = await subscriptions.list_for_user(viewer)
    assert any(item.template_code == 'email_confirmed' and item.enabled for item in saved.items)
