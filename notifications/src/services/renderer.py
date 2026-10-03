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

Песочница защищает от доступа к внутренностям, но не от расхода ресурсов:
`{{ 'x' * 10**9 }}`, `range` на миллионы шагов или вложенные циклы съедают
память и время **до** того, как письмо будет готово и его размер можно
проверить. Поэтому защита в три слоя:

1. **урезанный язык шаблонов** (`RestrictedEnvironment`): умножение строк и
   степени ограничены, `range` короткий, из фильтров и функций оставлено
   только то, что не умеет раздувать строку;
2. **размер считается по ходу сборки**, а не по готовому письму: сборка
   останавливается, как только письмо перевалило за предел;
3. **сборка идёт в отдельном процессе** с пределом памяти и времени
   (`services/render_sandbox.py`). Если шаблон всё же нашёл, чем занять
   процессор, процесс убивается, а API и воркер продолжают работать.

Третий слой здесь главный: первые два закрывают известные приёмы, а он —
все остальные, включая те, о которых мы не подумали.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from jinja2 import TemplateSyntaxError, UndefinedError, meta
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
    # Письма о бронях билетов (сервис бронирования).
    'host_name', 'guest_name', 'starts_at', 'place', 'address', 'seats', 'seats_left', 'change',
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
    'host_name': 'Нео Андерсон', 'guest_name': 'Тринити', 'starts_at': '17.10.2026 19:00 (MSK)',
    'place': 'Кинотеатр «Октябрь», зал 3', 'address': 'Москва, Новый Арбат, 24', 'seats': 2, 'seats_left': 4,
    'change': 'created',
}
# Предел размера письма. Полтора мегабайта — заведомо больше любого
# разумного письма.
MAX_RENDERED_SIZE = 1_500_000
# Самый длинный `range`, который можно построить в шаблоне. Писем с тысячей
# пунктов не бывает, а `range(10**9)` — это способ занять процессор.
MAX_RANGE = 1000
# Предел показателя степени: `9 ** 99999999` — число из ста миллионов цифр,
# на подсчёт которого уходят минуты.
MAX_POWER = 64
# Фильтры, которые не умеют раздувать строку сверх длины входа. Нет `center`,
# `indent`, `wordwrap`, `format`, `replace` — у всех есть аргумент, которым
# одна строка превращается в гигабайт.
SAFE_FILTERS = frozenset({
    'abs', 'capitalize', 'default', 'd', 'e', 'escape', 'first', 'float', 'int', 'join',
    'last', 'length', 'count', 'list', 'lower', 'upper', 'title', 'trim', 'round',
    'string', 'striptags', 'truncate', 'urlencode', 'safe', 'sort', 'unique', 'reverse',
    'min', 'max', 'sum', 'batch', 'slice', 'select', 'reject', 'map',
})


class RenderedTooLargeError(TemplateInvalidError):
    """Письмо выросло больше предела."""

    message = 'Rendered message is too large'


class RestrictedEnvironment(SandboxedEnvironment):
    """Песочница, в которой шаблон не может раздуть строку или число.

    `SandboxedEnvironment` умеет перехватывать бинарные операции — этим и
    пользуемся: умножение последовательности на число и возведение в степень
    проверяются до вычисления, а не после.
    """

    intercepted_binops = frozenset({'*', '**'})

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.filters = {name: func for name, func in self.filters.items() if name in SAFE_FILTERS}
        # Из глобальных функций Jinja2 оставлен только `range`, и тот короткий.
        # `lipsum(n=10**6)` и `cycler` в письме не нужны.
        self.globals = {'range': safe_range}

    def call_binop(self, context: Any, operator: str, left: Any, right: Any) -> Any:
        if operator == '*':
            _check_repeat(left, right)
            _check_repeat(right, left)
            return left * right
        if operator == '**':
            if isinstance(right, (int, float)) and abs(right) > MAX_POWER:
                raise TemplateInvalidError(f'Exponent is limited to {MAX_POWER}')
            return left ** right
        return super().call_binop(context, operator, left, right)


def _check_repeat(sequence: Any, times: Any) -> None:
    if isinstance(sequence, (str, list, tuple)) and isinstance(times, int):
        if len(sequence) * times > MAX_RENDERED_SIZE:
            raise RenderedTooLargeError


def safe_range(*args: int) -> range:
    """`range`, который не строит больше `MAX_RANGE` шагов."""
    produced = range(*args)
    if len(produced) > MAX_RANGE:
        raise TemplateInvalidError(f'range() is limited to {MAX_RANGE} items')
    return produced


class Renderer:
    """Сборка писем и проверка шаблонов в текущем процессе.

    Сам по себе он не защищён от шаблона, нашедшего, чем занять процессор, —
    поэтому сервисы зовут его не напрямую, а через `TemplateEngine`, который
    в работе запускает его в отдельном процессе.
    """

    def __init__(self, environment: SandboxedEnvironment | None = None) -> None:
        self._env = environment or build_environment()

    def render(self, template: Template, recipient: Recipient, context: dict[str, Any]) -> tuple[str, str]:
        """Собирает тему и тело письма для получателя.

        Raises:
            TemplateInvalidError: шаблон не собирается на этих данных.
        """
        return self.render_source(template.subject, template.body, letter_data(recipient, context))

    def render_source(self, subject: str, body: str, data: dict[str, Any]) -> tuple[str, str]:
        """Собирает письмо из исходников шаблона и готовых данных."""
        return self._render_one(subject, data), self._render_one(body, data)

    def validate(self, subject: str, body: str) -> None:
        """Проверяет шаблон менеджера перед сохранением.

        Три проверки из урока: синтаксис разбирается, использованы только
        разрешённые переменные, письмо действительно рендерится.

        Raises:
            TemplateInvalidError: любая из проверок не прошла.
        """
        for part, name in ((subject, 'subject'), (body, 'body')):
            used = self.variables_of(part, name)
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

        Raises:
            TemplateInvalidError: шаблон не собирается.
        """
        return self._render_one(subject, PROBE_CONTEXT), self._render_one(body, PROBE_CONTEXT)

    def uses(self, template: Template, variable: str) -> bool:
        """Встречается ли переменная в теме или теле шаблона.

        Нужно, чтобы не выдавать зря то, что стоит места в базе: токен
        подтверждения почты заводится, только если шаблон его выводит.
        Разбор шаблона его не исполняет, поэтому безопасен в любом процессе.
        """
        return any(variable in self.variables_of(part, name) for part, name in (
            (template.subject, 'subject'), (template.body, 'body'),
        ))

    def variables_of(self, source: str, part: str) -> set[str]:
        try:
            # Поиск переменных компилирует шаблон, и неизвестный фильтр
            # (`center`, `replace`) всплывает именно здесь, а не при разборе.
            return set(meta.find_undeclared_variables(self._env.parse(source)))
        except TemplateSyntaxError as error:
            raise TemplateInvalidError(f'Syntax error in {part}: {error.message} (line {error.lineno})') from error

    def _render_one(self, source: str, data: dict[str, Any]) -> str:
        try:
            compiled = self._env.from_string(source)
            # Письмо собирается кусками, и размер проверяется на каждом: шаблон,
            # раздувающий письмо циклом, останавливается на пределе, а не после
            # того, как съел память целиком.
            parts: list[str] = []
            size = 0
            for chunk in compiled.generate(**data):
                size += len(chunk)
                if size > MAX_RENDERED_SIZE:
                    raise RenderedTooLargeError
                parts.append(chunk)
        except TemplateInvalidError:
            raise
        except TemplateSyntaxError as error:
            raise TemplateInvalidError(f'Syntax error: {error.message} (line {error.lineno})') from error
        except UndefinedError as error:
            raise TemplateInvalidError(f'Undefined variable: {error.message}') from error
        except MemoryError as error:
            raise TemplateInvalidError('Template needs too much memory') from error
        except Exception as error:  # noqa: BLE001 - шаблон пишет человек, упасть он может как угодно
            raise TemplateInvalidError(f'Template failed to render: {error}') from error
        return ''.join(parts)


class TemplateEngine(ABC):
    """Сборка писем так, как её видят сервисы: асинхронно и с изоляцией.

    Сервисам всё равно, где именно собирается письмо. В работе это отдельный
    процесс с пределами памяти и времени, в unit-тестах — тот же процесс.
    """

    def __init__(self) -> None:
        # Разбор шаблона ничего не исполняет, поэтому идёт на месте.
        self._parser = Renderer()

    def uses(self, template: Template, variable: str) -> bool:
        return self._parser.uses(template, variable)

    @abstractmethod
    async def validate(self, subject: str, body: str) -> None:
        """Проверяет шаблон менеджера. Raises: TemplateInvalidError."""

    @abstractmethod
    async def preview(self, subject: str, body: str) -> tuple[str, str]:
        """Письмо на тестовых данных. Raises: TemplateInvalidError."""

    @abstractmethod
    async def render_many(
        self, template: Template, letters: Sequence[dict[str, Any]],
    ) -> list[tuple[str, str] | TemplateInvalidError]:
        """Собирает пачку писем одним вызовом.

        Ошибка одного письма не роняет пачку: на его месте в ответе лежит
        исключение. Пачка целиком, а не по письму, — чтобы переход в
        отдельный процесс случался раз на пачку, а не тысячу раз.

        Raises:
            TemplateInvalidError: сборка пачки не уложилась в пределы целиком.
        """

    async def close(self) -> None:  # noqa: B027 - закрывать есть что не у всех
        """Освобождает ресурсы движка."""


class InlineEngine(TemplateEngine):
    """Сборка в текущем процессе — для unit-тестов бизнес-логики."""

    def __init__(self, renderer: Renderer | None = None) -> None:
        super().__init__()
        self._renderer = renderer or Renderer()

    async def validate(self, subject: str, body: str) -> None:
        self._renderer.validate(subject, body)

    async def preview(self, subject: str, body: str) -> tuple[str, str]:
        self._renderer.validate(subject, body)
        return self._renderer.render_probe(subject, body)

    async def render_many(
        self, template: Template, letters: Sequence[dict[str, Any]],
    ) -> list[tuple[str, str] | TemplateInvalidError]:
        results: list[tuple[str, str] | TemplateInvalidError] = []
        for data in letters:
            try:
                results.append(self._renderer.render_source(template.subject, template.body, data))
            except TemplateInvalidError as error:
                results.append(error)
        return results


# Значения по умолчанию для переменных, которых может не оказаться в данных
# конкретного письма. Без них строгий режим Jinja2 уронил бы рендер на
# необязательной переменной, а письмо важнее аккуратности шаблона.
PROBE_CONTEXT_DEFAULTS: dict[str, Any] = {
    'first_name': '', 'last_name': '', 'full_name': '', 'email': '', 'login': '',
    'site_url': '', 'unsubscribe_url': '', 'action_url': '', 'confirm_url': '', 'subject': '',
    'film_title': '', 'episode': '', 'count': 0, 'items': [], 'year': '', 'month': '',
    'host_name': '', 'guest_name': '', 'starts_at': '', 'place': '', 'address': '', 'seats': 0, 'seats_left': 0,
    'change': '',
}


def build_environment() -> SandboxedEnvironment:
    """Урезанная песочница Jinja2 с автоэкранированием.

    `autoescape` включён: в письмо подставляются имя зрителя и название
    фильма, то есть данные, которые ввёл человек. Без экранирования
    подставленный HTML попал бы в письмо как разметка.
    """
    return RestrictedEnvironment(autoescape=True, trim_blocks=True, lstrip_blocks=True)


def letter_data(recipient: Recipient, context: dict[str, Any]) -> dict[str, Any]:
    """Всё, что подставляется в письмо получателю.

    Данные получателя идут последними: событие не может подменить имя или
    адрес зрителя своим `context`.
    """
    return {**PROBE_CONTEXT_DEFAULTS, **context, **recipient_context(recipient)}


def recipient_context(recipient: Recipient) -> dict[str, Any]:
    """Данные получателя для шаблона."""
    return {
        'first_name': recipient.first_name or '',
        'last_name': recipient.last_name or '',
        'full_name': recipient.full_name,
        'email': recipient.email or '',
    }
