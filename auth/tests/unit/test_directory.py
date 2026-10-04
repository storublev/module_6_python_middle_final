"""Справочник контактов: чтение пачкой, обход всех и правка своего профиля."""

from uuid import uuid4

import pytest

from models.user import ProfileUpdate
from services.directory import DirectoryService
from services.errors import UnknownTimezoneError
from tests.unit.fakes import FakeUserRepository


@pytest.fixture
def directory(users: FakeUserRepository) -> DirectoryService:
    return DirectoryService(users)


async def make_user(users: FakeUserRepository, login: str, **profile: str) -> str:
    user = await users.create(login=login, password_hash='hash')
    if profile:
        await users.update_profile(user.id, ProfileUpdate(**profile))
    return str(user.id)


async def test_contacts_returns_only_asked_users(directory: DirectoryService, users: FakeUserRepository) -> None:
    """Пачкой отдаются контакты именно запрошенных пользователей, чужие не попадают."""
    neo = await users.create(login='neo', password_hash='hash')
    await users.create(login='trinity', password_hash='hash')

    contacts = await directory.contacts([neo.id])

    assert [contact.login for contact in contacts] == ['neo']


async def test_contacts_skips_unknown_ids(directory: DirectoryService, users: FakeUserRepository) -> None:
    """Несуществующий идентификатор не ломает запрос: его просто нет в ответе."""
    neo = await users.create(login='neo', password_hash='hash')

    contacts = await directory.contacts([neo.id, uuid4()])

    assert [contact.id for contact in contacts] == [neo.id]


async def test_contacts_deduplicates_ids(directory: DirectoryService, users: FakeUserRepository) -> None:
    """Повтор идентификатора в запросе не превращается во второй контакт в ответе.

    Иначе рассылка отправила бы этому зрителю два письма: она считает письма
    по числу вернувшихся контактов.
    """
    neo = await users.create(login='neo', password_hash='hash')

    contacts = await directory.contacts([neo.id, neo.id, neo.id])

    assert len(contacts) == 1


async def test_contacts_carry_name_and_timezone(directory: DirectoryService, users: FakeUserRepository) -> None:
    """В контакте едет всё, из чего собирается письмо: почта, имя и часовой пояс."""
    neo = await users.create(login='neo', password_hash='hash')
    await users.update_profile(
        neo.id,
        ProfileUpdate(email='neo@example.com', first_name='Томас', last_name='Андерсон', timezone='Europe/Moscow'),
    )

    contact = (await directory.contacts([neo.id]))[0]

    assert contact.email == 'neo@example.com'
    assert contact.full_name == 'Томас Андерсон'
    assert contact.timezone == 'Europe/Moscow'


async def test_full_name_skips_missing_parts(directory: DirectoryService, users: FakeUserRepository) -> None:
    """Если фамилии нет, обращение не превращается в «Томас None» и не тянет лишний пробел."""
    neo = await users.create(login='neo', password_hash='hash')
    await users.update_profile(neo.id, ProfileUpdate(first_name='Томас'))

    contact = (await directory.contacts([neo.id]))[0]

    assert contact.full_name == 'Томас'


async def test_page_skips_users_without_email(directory: DirectoryService, users: FakeUserRepository) -> None:
    """Обход всех пользователей пропускает тех, кому некуда писать."""
    with_email = await users.create(login='neo', password_hash='hash')
    await users.update_profile(with_email.id, ProfileUpdate(email='neo@example.com'))
    await users.create(login='trinity', password_hash='hash')

    page = await directory.page(None, limit=10)

    assert [contact.login for contact in page] == ['neo']


async def test_page_walks_all_users_by_key(directory: DirectoryService, users: FakeUserRepository) -> None:
    """Листание по ключу обходит всех ровно один раз, без пропусков и повторов."""
    for number in range(5):
        user = await users.create(login=f'user{number}', password_hash='hash')
        await users.update_profile(user.id, ProfileUpdate(email=f'user{number}@example.com'))

    seen: list[str] = []
    after = None
    while True:
        page = await directory.page(after, limit=2)
        if not page:
            break
        seen.extend(contact.login for contact in page)
        after = page[-1].id

    assert sorted(seen) == ['user0', 'user1', 'user2', 'user3', 'user4']


async def test_update_profile_keeps_untouched_fields(directory: DirectoryService, users: FakeUserRepository) -> None:
    """Изменение одного поля не стирает остальные: None означает «не трогать»."""
    neo = await users.create(login='neo', password_hash='hash')
    await directory.update_profile(neo.id, ProfileUpdate(email='neo@example.com', first_name='Томас'))

    await directory.update_profile(neo.id, ProfileUpdate(first_name='Нео'))

    contact = (await directory.contacts([neo.id]))[0]
    assert contact.email == 'neo@example.com'
    assert contact.first_name == 'Нео'


async def test_update_profile_rejects_unknown_timezone(
    directory: DirectoryService, users: FakeUserRepository,
) -> None:
    """Неизвестный часовой пояс отклоняется сразу.

    Иначе он превратился бы не в понятный отказ, а в ошибку воркера рассылки,
    то есть в неотправленное письмо.
    """
    neo = await users.create(login='neo', password_hash='hash')

    with pytest.raises(UnknownTimezoneError):
        await directory.update_profile(neo.id, ProfileUpdate(timezone='Europe/Atlantis'))


async def test_update_profile_accepts_known_timezone(directory: DirectoryService, users: FakeUserRepository) -> None:
    """Имя из базы IANA принимается."""
    neo = await users.create(login='neo', password_hash='hash')

    await directory.update_profile(neo.id, ProfileUpdate(timezone='Asia/Vladivostok'))

    assert (await directory.contacts([neo.id]))[0].timezone == 'Asia/Vladivostok'
