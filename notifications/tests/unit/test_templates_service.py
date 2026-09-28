"""Шаблоны: CRUD и версии."""

import pytest

from models.enums import Channel
from models.notification import TemplateDraft
from services.errors import TemplateCodeTakenError, TemplateInvalidError, TemplateNotFoundError
from services.templates import TemplateService

DRAFT = TemplateDraft(
    code='welcome', name='Приветствие', channel=Channel.EMAIL,
    subject='Привет, {{ first_name }}', body='<p>{{ full_name }}</p>',
)


async def test_created_template_is_readable(template_service: TemplateService) -> None:
    """Созданный шаблон читается по коду."""
    await template_service.create(DRAFT)

    assert (await template_service.get('welcome')).subject == 'Привет, {{ first_name }}'


async def test_invalid_template_is_not_saved(template_service: TemplateService) -> None:
    """Шаблон с неизвестной переменной не сохраняется.

    Проверка идёт до записи: иначе негодный шаблон дожил бы до рассылки.
    """
    with pytest.raises(TemplateInvalidError):
        await template_service.create(DRAFT.model_copy(update={'body': '{{ password_hash }}'}))

    with pytest.raises(TemplateNotFoundError):
        await template_service.get('welcome')


async def test_duplicate_code_is_rejected(template_service: TemplateService) -> None:
    """Второй шаблон с тем же кодом создать нельзя."""
    await template_service.create(DRAFT)

    with pytest.raises(TemplateCodeTakenError):
        await template_service.create(DRAFT)


async def test_update_raises_version(template_service: TemplateService) -> None:
    """Правка повышает версию шаблона.

    По версии рассылка, начатая со старым текстом, досылается старым текстом.
    """
    created = await template_service.create(DRAFT)

    updated = await template_service.update('welcome', DRAFT.model_copy(update={'subject': 'Здравствуйте'}))

    assert (created.version, updated.version) == (1, 2)


async def test_old_version_stays_readable(template_service: TemplateService, templates_repo) -> None:
    """Прошлая версия шаблона остаётся доступной после правки."""
    await template_service.create(DRAFT)
    await template_service.update('welcome', DRAFT.model_copy(update={'subject': 'Здравствуйте'}))

    old = await templates_repo.get_version('welcome', 1)
    assert old is not None
    assert old.subject == 'Привет, {{ first_name }}'


async def test_update_of_missing_template_is_rejected(template_service: TemplateService) -> None:
    """Править несуществующий шаблон нельзя."""
    with pytest.raises(TemplateNotFoundError):
        await template_service.update('welcome', DRAFT)


async def test_delete_removes_template(template_service: TemplateService) -> None:
    """Удалённый шаблон больше не читается."""
    await template_service.create(DRAFT)

    await template_service.delete('welcome')

    with pytest.raises(TemplateNotFoundError):
        await template_service.get('welcome')


async def test_delete_of_missing_template_is_rejected(template_service: TemplateService) -> None:
    """Удалить несуществующий шаблон нельзя: менеджер должен видеть опечатку."""
    with pytest.raises(TemplateNotFoundError):
        await template_service.delete('welcome')


def test_preview_does_not_save(template_service: TemplateService) -> None:
    """Предпросмотр ничего не сохраняет: посмотреть можно и не сохраняя."""
    subject, body = template_service.preview(DRAFT)

    assert subject == 'Привет, Томас'
    assert 'Томас Андерсон' in body
