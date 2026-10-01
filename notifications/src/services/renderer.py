"""Сборка письма по шаблону и проверка шаблонов менеджера.

Шаблонизатор — **Jinja2**, как советует урок «Как устроен почтальон Печкин?»:
синтаксис как у Django-шаблонов, возможности шире, и он доступен вне Django.
Шаблон один и тот же для автоматических уведомлений и для рассылок менеджера —
этого прямо требует задание («единая система шаблонизации»).

Почему шаблон менеджера проверяется при сохранении. Менеджер — не
разработчик. Без проверки он однажды сохранит шаблон, который уронит сборку
письма у воркера или уйдёт в бесконечный цикл, и узнаем мы об этом во время
рассылки, когда половина писем уже ушла. Поэтому при сохранении шаблон
разбирается, сверяется с разрешённым набором переменных и **рендерится** на
тестовых данных.

Песочница `SandboxedEnvironment`, а не обычное окружение: в обычном из шаблона
доступны атрибуты объектов, а через них — внутренности процесса. Менеджеру
такого доступа быть не должно.
"""

import logging
from typing import Any

from jinja2 import TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

from models.notification import Recipient, Template
from services.errors import TemplateInvalidError

logger = logging.getLogger(__name__)

# Что менеджер вправе использовать в шаблоне. Набор намеренно маленький:
# урок предлагает ровно такой подход — «менеджерам достаточно иметь набор
# переменных, которыми они могут воспользоваться».
ALLOWED_VARIABLES = frozenset({
    'first_name', 'last_name', 'full_name', 'email', 'login',
    'site_url', 'unsubscribe_url', 'action_url', 'confirm_url', 'subject',
    'film_title', 'episode', 'count', 'items', 'year', 'month',
})
# Данные для пробного рендера при сохранении шаблона.
PROBE_CONTEXT: dict[str, Any] = {
    'first_name': 'Томас', 'last_name': 'Андерсон', 'full_name': 'Томас Андерсон',
    'email': 'neo@example.com', 'login': 'neo',
    'site_url': 'https://practix.local', 'unsubscribe_url': 'https://practix.local/s/abc1234',
    'action_url': 'https://practix.local/s/abc1234', 'confirm_url': 'https://practix.local/s/def5678',
    'subject': 'Проверка шаблона',
    'film_title': 'Матрица', 'episode': 8, 'count': 3, 'items': ['Матрица', 'Начало'],
    'year': 2026, 'month': 9,
}
# Предел размера письма: шаблон с циклом на миллион строк не должен съесть
# память воркера. Полтора мегабайта — заведомо больше любого разумного письма.
MAX_RENDERED_SIZE = 1_500_000


class Renderer:
    """Сборка писем и проверка шаблонов."""

    def __init__(self, environment: SandboxedEnvironment | None = None) -> None:
        self._env = environment or build_environment()

    def render(self, template: Template, recipient: Recipient, context: dict[str, Any]) -> tuple[str, str]:
        """Собирает тему и тело письма для получателя.

        Raises:
            TemplateInvalidError: шаблон не собирается на этих данных.
        """
        data = {**PROBE_CONTEXT_DEFAULTS, **context, **recipient_context(recipient)}
        return self._render_one(template.subject, data), self._render_one(template.body, data)

    def validate(self, subject: str, body: str) -> None:
        """Проверяет шаблон менеджера перед сохранением.

        Три проверки из урока: синтаксис разбирается, использованы только
        разрешённые переменные, письмо действительно рендерится.

        Raises:
            TemplateInvalidError: любая из проверок не прошла.
        """
        for part, name in ((subject, 'subject'), (body, 'body')):
            used = self._variables_of(part, name)
            unknown = sorted(used - ALLOWED_VARIABLES)
            if unknown:
                raise TemplateInvalidError(
                    f'Unknown variables in {name}: {", ".join(unknown)}. '
                    f'Allowed: {", ".join(sorted(ALLOWED_VARIABLES))}',
                )
        # Пробный рендер — последняя и самая честная проверка: синтаксически
        # верный шаблон всё равно может не собраться.
        self._render_one(subject, PROBE_CONTEXT)
        self._render_one(body, PROBE_CONTEXT)

    def render_probe(self, subject: str, body: str) -> tuple[str, str]:
        """Показывает, как письмо выглядит на тестовых данных.

        Нужно менеджеру: посмотреть письмо до того, как оно уйдёт миллиону
        зрителей, и не сохраняя шаблон.

        Raises:
            TemplateInvalidError: шаблон не собирается.
        """
        return self._render_one(subject, PROBE_CONTEXT), self._render_one(body, PROBE_CONTEXT)

    def uses(self, template: Template, variable: str) -> bool:
        """Встречается ли переменная в теме или теле шаблона.

        Нужно, чтобы не выдавать зря то, что стоит денег или места в базе:
        токен подтверждения почты заводится, только если шаблон его выводит.
        """
        return any(variable in self._variables_of(part, name) for part, name in (
            (template.subject, 'subject'), (template.body, 'body'),
        ))

    def _variables_of(self, source: str, part: str) -> set[str]:
        from jinja2 import meta

        try:
            parsed = self._env.parse(source)
        except TemplateSyntaxError as error:
            raise TemplateInvalidError(f'Syntax error in {part}: {error.message} (line {error.lineno})') from error
        return set(meta.find_undeclared_variables(parsed))

    def _render_one(self, source: str, data: dict[str, Any]) -> str:
        try:
            rendered = self._env.from_string(source).render(**data)
        except TemplateSyntaxError as error:
            raise TemplateInvalidError(f'Syntax error: {error.message} (line {error.lineno})') from error
        except UndefinedError as error:
            raise TemplateInvalidError(f'Undefined variable: {error.message}') from error
        except Exception as error:  # noqa: BLE001 - шаблон пишет человек, упасть он может как угодно
            raise TemplateInvalidError(f'Template failed to render: {error}') from error
        if len(rendered) > MAX_RENDERED_SIZE:
            raise TemplateInvalidError('Rendered message is too large')
        return rendered


# Значения по умолчанию для переменных, которых может не оказаться в данных
# конкретного письма. Без них строгий режим Jinja2 уронил бы рендер на
# необязательной переменной, а письмо важнее аккуратности шаблона.
PROBE_CONTEXT_DEFAULTS: dict[str, Any] = {
    'first_name': '', 'last_name': '', 'full_name': '', 'email': '', 'login': '',
    'site_url': '', 'unsubscribe_url': '', 'action_url': '', 'confirm_url': '', 'subject': '',
    'film_title': '', 'episode': '', 'count': 0, 'items': [], 'year': '', 'month': '',
}


def build_environment() -> SandboxedEnvironment:
    """Песочница Jinja2 с автоэкранированием.

    `autoescape` включён: в письмо подставляются имя зрителя и название
    фильма, то есть данные, которые ввёл человек. Без экранирования
    подставленный HTML попал бы в письмо как разметка.
    """
    return SandboxedEnvironment(autoescape=True, trim_blocks=True, lstrip_blocks=True)


def recipient_context(recipient: Recipient) -> dict[str, Any]:
    """Данные получателя для шаблона."""
    return {
        'first_name': recipient.first_name or '',
        'last_name': recipient.last_name or '',
        'full_name': recipient.full_name,
        'email': recipient.email or '',
    }
