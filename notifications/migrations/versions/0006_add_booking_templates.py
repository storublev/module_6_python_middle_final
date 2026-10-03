"""Шаблоны писем о бронях билетов на совместные просмотры.

Письма шлёт сервис бронирования через `POST /events` (ADR-23 в
docs/diploma/architecture.md). Шаблонов четыре: подтверждение гостю,
сообщение хосту о новой, изменённой или отменённой брони и два письма гостям
— о переносе и об отмене показа. Время показа приходит уже по-человечески
(«17.10.2026 19:00 (MSK)»): переводить его в пояс получателя шаблону нечем.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-03
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'notify'

GUEST_BODY = """<h1>{% if change == 'changed' %}Бронь изменена{% else %}Вы идёте в кино!{% endif %}</h1>
<p>{{ first_name or login }}, за вами {{ seats }} мест на «{{ film_title }}».</p>
<ul>
  <li>Когда: {{ starts_at }}</li>
  <li>Где: {{ place }}, {{ address }}</li>
  <li>Хост: {{ host_name }}</li>
</ul>
<p><a href="{{ action_url }}">Открыть показ</a> · <a href="{{ unsubscribe_url }}">Отписаться</a></p>
"""

HOST_BODY = """<h1>
{% if change == 'cancelled' %}Гость отменил бронь
{% elif change == 'changed' %}Гость изменил бронь
{% else %}Новая бронь{% endif %}
</h1>
<p>{{ first_name or login }}, {{ guest_name }}
{% if change == 'cancelled' %}больше не придёт{% else %}бронирует {{ seats }} мест{% endif %}
на «{{ film_title }}» {{ starts_at }}.</p>
<p>Свободных мест осталось: {{ seats_left }}.</p>
<p><a href="{{ action_url }}">Гости показа</a> · <a href="{{ unsubscribe_url }}">Отписаться</a></p>
"""

CHANGED_BODY = """<h1>Показ перенесён</h1>
<p>{{ first_name or login }}, хост {{ host_name }} изменил показ «{{ film_title }}».</p>
<ul>
  <li>Когда: {{ starts_at }}</li>
  <li>Где: {{ place }}, {{ address }}</li>
</ul>
<p>Бронь сохранена. Если новое время не подходит, её можно отменить на странице показа.</p>
<p><a href="{{ action_url }}">Открыть показ</a> · <a href="{{ unsubscribe_url }}">Отписаться</a></p>
"""

CANCELLED_BODY = """<h1>Показ отменён</h1>
<p>{{ first_name or login }}, к сожалению, хост {{ host_name }} отменил показ «{{ film_title }}» {{ starts_at }}.</p>
<p>Ваша бронь отменена. Посмотрите другие показы этого фильма — возможно, кто-то собирается в то же время.</p>
<p><a href="{{ site_url }}">Найти показ</a> · <a href="{{ unsubscribe_url }}">Отписаться</a></p>
"""

TEMPLATES = (
    ('booking_confirmed', 'Бронь: подтверждение гостю', 'Бронь на «{{ film_title }}» — {{ starts_at }}', GUEST_BODY),
    ('booking_host_update', 'Бронь: сообщение хосту', '{{ guest_name }}: бронь на «{{ film_title }}»', HOST_BODY),
    ('screening_changed', 'Показ перенесён', 'Показ «{{ film_title }}» изменён', CHANGED_BODY),
    ('screening_cancelled', 'Показ отменён', 'Показ «{{ film_title }}» отменён', CANCELLED_BODY),
)

templates = sa.table(
    'templates',
    sa.column('id', sa.Uuid()), sa.column('code', sa.String()), sa.column('name', sa.String()),
    sa.column('channel', sa.String()), sa.column('subject', sa.String()), sa.column('body', sa.Text()),
    sa.column('version', sa.Integer()), sa.column('is_active', sa.Boolean()),
    schema=SCHEMA,
)
versions = sa.table(
    'template_versions',
    sa.column('id', sa.Uuid()), sa.column('code', sa.String()), sa.column('version', sa.Integer()),
    sa.column('name', sa.String()), sa.column('channel', sa.String()),
    sa.column('subject', sa.String()), sa.column('body', sa.Text()),
    schema=SCHEMA,
)


def upgrade() -> None:
    for code, name, subject, body in TEMPLATES:
        op.bulk_insert(templates, [{
            'id': uuid.uuid4(), 'code': code, 'name': name, 'channel': 'email',
            'subject': subject, 'body': body, 'version': 1, 'is_active': True,
        }])
        op.bulk_insert(versions, [{
            'id': uuid.uuid4(), 'code': code, 'version': 1, 'name': name,
            'channel': 'email', 'subject': subject, 'body': body,
        }])


def downgrade() -> None:
    codes = [code for code, *_ in TEMPLATES]
    op.execute(sa.delete(versions).where(versions.c.code.in_(codes)))
    op.execute(sa.delete(templates).where(templates.c.code.in_(codes)))
