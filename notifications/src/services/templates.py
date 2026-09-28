"""Шаблоны писем: CRUD для админ-панели.

Единственное правило, ради которого этот слой вообще есть: **шаблон не
сохраняется, пока не проверен**. Менеджер — не разработчик, и без проверки он
однажды сохранит шаблон, который уронит сборку письма у воркера или уйдёт в
бесконечный цикл. Проверка описана в `services/renderer.py`.
"""

import logging

from models.notification import Template, TemplateDraft
from services.errors import TemplateCodeTakenError, TemplateNotFoundError
from services.renderer import Renderer
from storage.base import AlreadyExistsError, TemplateRepository

logger = logging.getLogger(__name__)


class TemplateService:
    """Управление шаблонами."""

    def __init__(self, templates: TemplateRepository, renderer: Renderer) -> None:
        self._templates = templates
        self._renderer = renderer

    async def list_all(self) -> list[Template]:
        return await self._templates.list_all()

    async def get(self, code: str) -> Template:
        """Отдаёт шаблон по коду.

        Raises:
            TemplateNotFoundError: шаблона нет.
        """
        template = await self._templates.get(code)
        if template is None:
            raise TemplateNotFoundError
        return template

    async def create(self, draft: TemplateDraft) -> Template:
        """Создаёт шаблон.

        Raises:
            TemplateInvalidError: шаблон не прошёл проверку.
            TemplateCodeTakenError: код занят.
        """
        self._renderer.validate(draft.subject, draft.body)
        try:
            created = await self._templates.create(draft)
        except AlreadyExistsError as error:
            raise TemplateCodeTakenError from error
        logger.info('Шаблон создан', extra={'template': created.code})
        return created

    async def update(self, code: str, draft: TemplateDraft) -> Template:
        """Меняет шаблон и повышает его версию.

        Raises:
            TemplateInvalidError: шаблон не прошёл проверку.
            TemplateNotFoundError: шаблона нет.
        """
        self._renderer.validate(draft.subject, draft.body)
        updated = await self._templates.update(code, draft)
        if updated is None:
            raise TemplateNotFoundError
        logger.info('Шаблон изменён', extra={'template': code, 'version': updated.version})
        return updated

    async def delete(self, code: str) -> None:
        """Удаляет шаблон.

        Raises:
            TemplateNotFoundError: шаблона нет.
        """
        if not await self._templates.delete(code):
            raise TemplateNotFoundError
        logger.info('Шаблон удалён', extra={'template': code})

    def preview(self, draft: TemplateDraft) -> tuple[str, str]:
        """Показывает, как письмо выглядит на тестовых данных.

        Нужно менеджеру: «проверить на себе» до того, как письмо уйдёт
        миллиону зрителей. Отдельная операция, а не побочный эффект
        сохранения, — посмотреть можно и не сохраняя.

        Raises:
            TemplateInvalidError: шаблон не прошёл проверку.
        """
        self._renderer.validate(draft.subject, draft.body)
        return self._renderer.render_probe(draft.subject, draft.body)
