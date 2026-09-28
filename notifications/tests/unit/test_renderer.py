"""Шаблонизатор: проверка шаблона менеджера и сборка письма."""

import pytest

from services.errors import TemplateInvalidError
from services.renderer import Renderer
from tests.unit.fakes import recipient, template


def test_valid_template_passes(renderer: Renderer) -> None:
    """Шаблон из разрешённых переменных сохраняется."""
    renderer.validate('Привет, {{ first_name }}!', '<p>{{ full_name }}, смотрите {{ film_title }}</p>')


def test_syntax_error_is_rejected(renderer: Renderer) -> None:
    """Шаблон с ошибкой синтаксиса не сохраняется.

    Иначе он уронил бы сборку письма у воркера, и узнали бы мы об этом во
    время рассылки.
    """
    with pytest.raises(TemplateInvalidError):
        renderer.validate('Привет', '<p>{% if first_name %}нет конца')


def test_unknown_variable_is_rejected(renderer: Renderer) -> None:
    """Переменная, которой у нас нет, не сохраняется.

    Урок предлагает ровно это: менеджеру доступен ограниченный набор
    переменных, и проверять их — работа сервиса.
    """
    with pytest.raises(TemplateInvalidError) as error:
        renderer.validate('Привет', '<p>{{ password_hash }}</p>')

    assert 'password_hash' in str(error.value)


def test_oversized_render_is_rejected(renderer: Renderer) -> None:
    """Шаблон, раздувающий письмо до неразумного размера, не сохраняется.

    В Jinja2 нет цикла `while`, поэтому «вечный цикл», которым пугает урок,
    проявляется именно так — письмом на десятки мегабайт. Ловим это при
    сохранении, а не в рассылке, когда память кончится у воркера.
    """
    with pytest.raises(TemplateInvalidError):
        renderer.validate('Привет', "{{ 'x' * 10000000 }}")


def test_sandbox_blocks_access_to_internals(renderer: Renderer) -> None:
    """Из шаблона нельзя дотянуться до внутренностей процесса.

    Обычное окружение Jinja2 даёт доступ к атрибутам объектов, а через них —
    куда угодно. Менеджеру такого доступа быть не должно.
    """
    with pytest.raises(TemplateInvalidError):
        renderer.validate('Привет', "{{ items.__class__.__mro__ }}")


def test_html_in_data_is_escaped(renderer: Renderer) -> None:
    """HTML в имени зрителя попадает в письмо как текст, а не как разметка."""
    subject, body = renderer.render(
        template(subject='Привет', body='<p>{{ first_name }}</p>'),
        recipient(first_name='<script>alert(1)</script>'),
        {},
    )

    assert '<script>' not in body
    assert '&lt;script&gt;' in body


def test_missing_optional_variable_does_not_break_render(renderer: Renderer) -> None:
    """Необязательная переменная, которой нет в данных письма, не роняет сборку.

    Письмо важнее аккуратности шаблона: лучше отправить без названия фильма,
    чем не отправить вовсе.
    """
    subject, body = renderer.render(
        template(subject='Привет', body='<p>{{ film_title }}{{ episode }}</p>'),
        recipient(),
        {},
    )

    assert body == '<p></p>'


def test_render_substitutes_recipient_data(renderer: Renderer) -> None:
    """Данные получателя подставляются в шаблон."""
    subject, body = renderer.render(
        template(subject='{{ first_name }}, привет', body='<p>{{ email }}</p>'),
        recipient(first_name='Нео', email='neo@example.com'),
        {},
    )

    assert subject == 'Нео, привет'
    assert 'neo@example.com' in body


def test_context_does_not_override_recipient(renderer: Renderer) -> None:
    """Данные события не могут подменить данные получателя.

    Иначе рассылка с `context={'email': ...}` подставила бы всем один адрес.
    """
    subject, body = renderer.render(
        template(subject='{{ email }}', body='<p>{{ email }}</p>'),
        recipient(email='real@example.com'),
        {'email': 'fake@example.com'},
    )

    assert subject == 'real@example.com'


def test_preview_renders_on_probe_data(renderer: Renderer) -> None:
    """Предпросмотр собирает письмо на подставных данных, ничего не сохраняя."""
    subject, body = renderer.render_probe('Привет, {{ first_name }}', '<p>{{ film_title }}</p>')

    assert subject == 'Привет, Томас'
    assert 'Матрица' in body
