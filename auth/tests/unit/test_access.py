"""Роли и проверка прав: суперпользователь, аноним, кеш и его сброс."""

from uuid import uuid4

import pytest

from models.role import FILMS_SUBSCRIPTION, MANAGE_ACCESS, Role
from models.user import User
from services.access import AccessService
from services.auth import Principal, RegistrationService
from services.errors import (
    PermissionDeniedError,
    RoleNameTakenError,
    RoleNotAssignedError,
    RoleNotFoundError,
    UserNotFoundError,
)
from services.roles import RoleService
from tests.unit.conftest import PASSWORD
from tests.unit.fakes import FakeAccessCache, FakeUserRepository


@pytest.fixture
async def user(registration: RegistrationService) -> User:
    return await registration.register('neo', PASSWORD)


@pytest.fixture
def principal(user: User) -> Principal:
    return Principal(user_id=user.id, session_id=uuid4())


@pytest.fixture
async def subscribers(role_service: RoleService) -> Role:
    return await role_service.create_role('subscribers', None, [FILMS_SUBSCRIPTION])


async def test_anonymous_has_no_permissions(access: AccessService) -> None:
    """Анонимному пользователю не выдано ни одно право."""
    assert not await access.check(None, FILMS_SUBSCRIPTION)


async def test_user_without_roles_has_no_permissions(access: AccessService, principal: Principal) -> None:
    """У пользователя без ролей прав нет."""
    assert not await access.check(principal, FILMS_SUBSCRIPTION)


async def test_superuser_is_allowed_everything(access: AccessService, registration: RegistrationService) -> None:
    """Суперпользователю разрешено любое право, даже не выданное ни одной роли."""
    admin = await registration.register('admin', PASSWORD, is_superuser=True)
    principal = Principal(user_id=admin.id, session_id=uuid4())

    assert await access.check(principal, 'anything.at_all')
    await access.require(principal, MANAGE_ACCESS)


async def test_assigned_role_grants_permissions_at_once(
    access: AccessService, role_service: RoleService, principal: Principal, subscribers: Role,
) -> None:
    """Права роли действуют сразу после назначения, даже если прошлый ответ «нет» лежит в кеше."""
    assert not await access.check(principal, FILMS_SUBSCRIPTION)

    await role_service.assign(principal.user_id, subscribers.id)

    assert await access.check(principal, FILMS_SUBSCRIPTION)


async def test_revoked_role_takes_permissions_at_once(
    access: AccessService, role_service: RoleService, principal: Principal, subscribers: Role,
) -> None:
    """После отзыва роли её права пропадают сразу."""
    await role_service.assign(principal.user_id, subscribers.id)
    assert await access.check(principal, FILMS_SUBSCRIPTION)

    await role_service.revoke(principal.user_id, subscribers.id)

    assert not await access.check(principal, FILMS_SUBSCRIPTION)


async def test_role_changes_apply_to_all_holders(
    access: AccessService, role_service: RoleService, principal: Principal, subscribers: Role,
) -> None:
    """Изменение прав роли и её удаление сразу действуют у всех, кому она назначена."""
    await role_service.assign(principal.user_id, subscribers.id)
    assert not await access.check(principal, 'films.premium')

    await role_service.update_role(subscribers.id, {'permissions': [FILMS_SUBSCRIPTION, 'films.premium']})
    assert await access.check(principal, 'films.premium')

    await role_service.delete_role(subscribers.id)
    assert not await access.check(principal, FILMS_SUBSCRIPTION)


async def test_permissions_are_read_from_cache(
    access: AccessService, users: FakeUserRepository, cache: FakeAccessCache, principal: Principal,
) -> None:
    """Повторные проверки берут права из кеша и не ходят в базу."""
    for _ in range(5):
        await access.check(principal, FILMS_SUBSCRIPTION)

    assert users.access_reads == 1
    assert principal.user_id in cache.entries


async def test_require_raises_without_permission(access: AccessService, principal: Principal) -> None:
    """require() без права поднимает PermissionDeniedError с именем права."""
    with pytest.raises(PermissionDeniedError, match=MANAGE_ACCESS):
        await access.require(principal, MANAGE_ACCESS)


async def test_deleted_user_has_no_permissions(
    access: AccessService, role_service: RoleService, principal: Principal, subscribers: Role, db,
) -> None:
    """Если пользователя удалили, пока его токен действует, прав у него нет."""
    await role_service.assign(principal.user_id, subscribers.id)
    db.users.clear()

    assert not await access.check(principal, FILMS_SUBSCRIPTION)


async def test_create_role_with_taken_name_raises(role_service: RoleService, subscribers: Role) -> None:
    """Имя роли уникально."""
    with pytest.raises(RoleNameTakenError):
        await role_service.create_role('subscribers', None, [])


async def test_create_role_drops_duplicate_permissions(role_service: RoleService) -> None:
    """Повторы в списке прав не сохраняются, порядок остаётся."""
    role = await role_service.create_role('editors', None, ['b.x', 'a.x', 'b.x'])

    assert role.permissions == ('b.x', 'a.x')


async def test_update_role(role_service: RoleService, subscribers: Role) -> None:
    """Меняются только переданные поля роли."""
    role = await role_service.update_role(subscribers.id, {'description': 'Подписчики'})

    assert role.description == 'Подписчики'
    assert role.name == 'subscribers'
    assert role.permissions == (FILMS_SUBSCRIPTION,)


async def test_rename_role_to_taken_name_raises(role_service: RoleService, subscribers: Role) -> None:
    """Переименовать роль в имя другой роли нельзя."""
    editors = await role_service.create_role('editors', None, [])

    with pytest.raises(RoleNameTakenError):
        await role_service.update_role(editors.id, {'name': 'subscribers'})


async def test_missing_role_raises(role_service: RoleService, principal: Principal) -> None:
    """Операции с несуществующей ролью — RoleNotFoundError."""
    role_id = uuid4()
    for call in (
        role_service.get_role(role_id),
        role_service.update_role(role_id, {'description': 'x'}),
        role_service.delete_role(role_id),
        role_service.assign(principal.user_id, role_id),
        role_service.revoke(principal.user_id, role_id),
    ):
        with pytest.raises(RoleNotFoundError):
            await call


async def test_missing_user_raises(role_service: RoleService, subscribers: Role) -> None:
    """Назначить, отобрать или показать роли несуществующего пользователя нельзя."""
    user_id = uuid4()
    for call in (
        role_service.user_roles(user_id),
        role_service.assign(user_id, subscribers.id),
        role_service.revoke(user_id, subscribers.id),
    ):
        with pytest.raises(UserNotFoundError):
            await call


async def test_assign_twice_and_revoke_not_assigned(
    role_service: RoleService, principal: Principal, subscribers: Role,
) -> None:
    """Повторное назначение ничего не меняет, отзыв неназначенной роли — RoleNotAssignedError."""
    await role_service.assign(principal.user_id, subscribers.id)
    await role_service.assign(principal.user_id, subscribers.id)
    assert await role_service.user_roles(principal.user_id) == [subscribers]

    await role_service.revoke(principal.user_id, subscribers.id)
    with pytest.raises(RoleNotAssignedError):
        await role_service.revoke(principal.user_id, subscribers.id)
