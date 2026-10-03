"""Подтверждение почты одноразовым токеном и отдельно от подписок.

До этой миграции адрес подтверждался по одному `user_id` в ссылке, а отметка
ставилась подпиской `email_confirmed` — и заодно снимала общий отказ от
рассылок. Кто знал чужой идентификатор, мог и «подтвердить» чужой адрес, и
вернуть рассылки отписавшемуся зрителю.

Теперь:

* `email_confirmation_tokens` — выданные токены (хранится SHA-256), с адресом,
  сроком и отметкой погашения;
* `email_confirmations` — подтверждённый адрес зрителя, отдельно от подписок;
* служебные подписки `email_confirmed` удаляются: они больше ничего не
  значат, а в настройках зрителя выглядели бы типом уведомлений;
* приветственное письмо получает ссылку из новой переменной `confirm_url`.
  Раньше в нём стоял `action_url`, который сборщик не заполнял, и кнопка
  подтверждения вела в никуда. Шаблон меняется, только если менеджер его не
  правил, и получает новую версию — как при правке из админ-панели.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'notify'
OLD_LINK = '{{ action_url }}'
NEW_LINK = '{{ confirm_url }}'

subscriptions = sa.table('subscriptions', sa.column('template_code', sa.String()), schema=SCHEMA)
templates = sa.table(
    'templates',
    sa.column('code', sa.String()), sa.column('version', sa.Integer()), sa.column('name', sa.String()),
    sa.column('channel', sa.String()), sa.column('subject', sa.String()), sa.column('body', sa.Text()),
    sa.column('updated_at', sa.DateTime(timezone=True)),
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
    op.create_table(
        'email_confirmation_tokens',
        sa.Column('token_hash', sa.String(64), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('token_hash', name='pk_email_confirmation_tokens'),
        schema=SCHEMA,
    )
    op.create_table(
        'email_confirmations',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('user_id', name='pk_email_confirmations'),
        schema=SCHEMA,
    )
    op.execute(sa.delete(subscriptions).where(subscriptions.c.template_code == 'email_confirmed'))
    _replace_welcome_link(OLD_LINK, NEW_LINK)


def downgrade() -> None:
    _replace_welcome_link(NEW_LINK, OLD_LINK)
    op.drop_table('email_confirmations', schema=SCHEMA)
    op.drop_table('email_confirmation_tokens', schema=SCHEMA)


def _replace_welcome_link(old: str, new: str) -> None:
    """Меняет ссылку в приветственном письме и заводит новую версию шаблона.

    Версия растёт, как при правке из админ-панели: рассылка, начатая со
    старым текстом, досылается старым текстом.
    """
    bind = op.get_bind()
    row = bind.execute(
        sa.update(templates)
        .where(templates.c.code == 'welcome', templates.c.body.contains(old, autoescape=True))
        .values(
            body=sa.func.replace(templates.c.body, old, new),
            version=templates.c.version + 1,
            updated_at=sa.func.now(),
        )
        .returning(
            templates.c.code, templates.c.version, templates.c.name,
            templates.c.channel, templates.c.subject, templates.c.body,
        ),
    ).first()
    if row is None:
        return
    bind.execute(sa.insert(versions).values(id=uuid.uuid4(), **row._mapping))
