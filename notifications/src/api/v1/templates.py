"""Шаблоны писем: CRUD для админ-панели.

Шаблон проверяется **при сохранении**, а не при рассылке: менеджер — не
разработчик, и без проверки он однажды сохранит шаблон с бесконечным циклом
или с переменной, которой у нас нет.
"""

from http import HTTPStatus

from fastapi import APIRouter

from api.dependencies import ServiceToken, TemplateServiceDep
from api.errors import error_responses
from api.v1.schemas import TemplateDraftSchema, TemplatePreviewSchema, TemplateSchema
from models.notification import TemplateDraft
from services.errors import (
    ServiceTokenInvalidError,
    TemplateCodeTakenError,
    TemplateInvalidError,
    TemplateNotFoundError,
)
from services.renderer import ALLOWED_VARIABLES

router = APIRouter()

VARIABLES_HELP = (
    'В шаблоне доступны только эти переменные: '
    + ', '.join(f'`{name}`' for name in sorted(ALLOWED_VARIABLES))
    + '. Шаблон с другими переменными, с ошибкой синтаксиса или с бесконечным циклом не сохранится.'
)


@router.get(
    '',
    response_model=list[TemplateSchema],
    summary='Все шаблоны',
    responses=error_responses(ServiceTokenInvalidError),
)
async def list_templates(templates: TemplateServiceDep, _: ServiceToken = None) -> list[TemplateSchema]:
    return [TemplateSchema.model_validate(item) for item in await templates.list_all()]


@router.post(
    '',
    response_model=TemplateSchema,
    status_code=HTTPStatus.CREATED,
    summary='Создать шаблон',
    description=VARIABLES_HELP,
    responses=error_responses(ServiceTokenInvalidError, TemplateInvalidError, TemplateCodeTakenError),
)
async def create_template(
    body: TemplateDraftSchema, templates: TemplateServiceDep, _: ServiceToken = None,
) -> TemplateSchema:
    created = await templates.create(TemplateDraft.model_validate(body.model_dump()))
    return TemplateSchema.model_validate(created)


@router.post(
    '/preview',
    response_model=TemplatePreviewSchema,
    summary='Посмотреть письмо на тестовых данных',
    description='Собирает письмо на подставных данных, ничего не сохраняя. ' + VARIABLES_HELP,
    responses=error_responses(ServiceTokenInvalidError, TemplateInvalidError),
)
async def preview_template(
    body: TemplateDraftSchema, templates: TemplateServiceDep, _: ServiceToken = None,
) -> TemplatePreviewSchema:
    subject, rendered = templates.preview(TemplateDraft.model_validate(body.model_dump()))
    return TemplatePreviewSchema(subject=subject, body=rendered)


@router.get(
    '/{code}',
    response_model=TemplateSchema,
    summary='Шаблон по коду',
    responses=error_responses(ServiceTokenInvalidError, TemplateNotFoundError),
)
async def get_template(code: str, templates: TemplateServiceDep, _: ServiceToken = None) -> TemplateSchema:
    return TemplateSchema.model_validate(await templates.get(code))


@router.put(
    '/{code}',
    response_model=TemplateSchema,
    summary='Изменить шаблон',
    description='Правка повышает версию шаблона. Рассылка, начатая со старым текстом, досылается старым текстом. '
                + VARIABLES_HELP,
    responses=error_responses(ServiceTokenInvalidError, TemplateInvalidError, TemplateNotFoundError),
)
async def update_template(
    code: str, body: TemplateDraftSchema, templates: TemplateServiceDep, _: ServiceToken = None,
) -> TemplateSchema:
    updated = await templates.update(code, TemplateDraft.model_validate(body.model_dump()))
    return TemplateSchema.model_validate(updated)


@router.delete(
    '/{code}',
    status_code=HTTPStatus.NO_CONTENT,
    summary='Удалить шаблон',
    responses=error_responses(ServiceTokenInvalidError, TemplateNotFoundError),
)
async def delete_template(code: str, templates: TemplateServiceDep, _: ServiceToken = None) -> None:
    await templates.delete(code)
