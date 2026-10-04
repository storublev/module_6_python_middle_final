"""Таблицы сервиса уведомлений и стартовые шаблоны писем.

Три шаблона — те самые, что принёс менеджер в уроке «Гарри, тебе письмо!»:
недельная подборка всем, итоги месяца активным зрителям и приветственное
письмо после регистрации. Они заводятся миграцией, чтобы стенд поднимался
готовым к работе, а не требовал ручного создания шаблонов перед первой
проверкой.

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = 'notify'

WELCOME_BODY = """<h1>Добро пожаловать в Practix, {{ first_name or login }}!</h1>
<p>Спасибо за регистрацию. Подтвердите адрес почты, чтобы получать подборки фильмов:</p>
<p><a href="{{ action_url }}">Подтвердить адрес</a></p>
<p><a href="{{ unsubscribe_url }}">Отписаться от писем</a></p>
"""

DIGEST_BODY = """<h1>Подборка недели</h1>
<p>Здравствуйте{% if first_name %}, {{ first_name }}{% endif %}! Вот что стоит посмотреть на выходных:</p>
<ul>
{% for item in items %}
  <li>{{ item }}</li>
{% endfor %}
</ul>
<p><a href="{{ site_url }}">Открыть кинотеатр</a> · <a href="{{ unsubscribe_url }}">Отписаться</a></p>
"""

MONTHLY_BODY = """<h1>Ваш месяц в Practix</h1>
<p>{{ full_name or login }}, в этом месяце вы посмотрели {{ count }} фильмов.</p>
<p>Любимые жанры: {% for item in items %}{{ item }}{% if not loop.last %}, {% endif %}{% endfor %}.</p>
<p><a href="{{ site_url }}">Продолжить смотреть</a> · <a href="{{ unsubscribe_url }}">Отписаться</a></p>
"""

EPISODE_BODY = """<h1>Вышла новая серия</h1>
<p>{{ first_name or 'Здравствуйте' }}, у сериала «{{ film_title }}» вышла серия {{ episode }}.</p>
<p><a href="{{ site_url }}">Смотреть</a> · <a href="{{ unsubscribe_url }}">Отписаться</a></p>
"""

TEMPLATES = (
    ('welcome', 'Приветственное письмо', 'Добро пожаловать в Practix!', WELCOME_BODY),
    ('weekly_digest', 'Недельная подборка', 'Подборка фильмов на выходные', DIGEST_BODY),
    ('monthly_summary', 'Итоги месяца', 'Ваш месяц в Practix', MONTHLY_BODY),
    ('new_episode', 'Новая серия сериала', 'Вышла новая серия «{{ film_title }}»', EPISODE_BODY),
)


def upgrade() -> None:
    json_type = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        'events',
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('routing_key', sa.String(128), nullable=False),
        sa.Column('template_code', sa.String(64), nullable=False),
        sa.Column('payload', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('event_id', name='pk_events'),
        schema=SCHEMA,
    )

    op.create_table(
        'templates',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(64), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('channel', sa.String(16), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_templates'),
        sa.UniqueConstraint('code', name='uq_templates_code'),
        schema=SCHEMA,
    )

    op.create_table(
        'template_versions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('channel', sa.String(16), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_template_versions'),
        sa.UniqueConstraint('code', 'version', name='uq_template_versions_code_version'),
        schema=SCHEMA,
    )

    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('template_code', sa.String(64), nullable=False),
        sa.Column('channel', sa.String(16), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_subscriptions'),
        sa.UniqueConstraint(
            'user_id', 'template_code', 'channel',
            name='uq_subscriptions_user_id_template_code_channel',
        ),
        schema=SCHEMA,
    )

    op.create_table(
        'notifications',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('template_code', sa.String(64), nullable=False),
        sa.Column('content_id', sa.String(128), nullable=False),
        sa.Column('content_version', sa.Integer(), nullable=True),
        sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_notifications'),
        sa.UniqueConstraint(
            'user_id', 'template_code', 'content_id',
            name='uq_notifications_user_id_template_code_content_id',
        ),
        schema=SCHEMA,
    )

    op.create_table(
        'deliveries',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('idempotency_key', sa.String(255), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('channel', sa.String(16), nullable=False),
        sa.Column('template_code', sa.String(64), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_deliveries'),
        sa.UniqueConstraint('idempotency_key', name='uq_deliveries_idempotency_key'),
        schema=SCHEMA,
    )
    op.create_index('ix_deliveries_user_id_created_at', 'deliveries', ['user_id', 'created_at'], schema=SCHEMA)

    op.create_table(
        'campaigns',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('template_code', sa.String(64), nullable=False),
        sa.Column('channel', sa.String(16), nullable=False),
        sa.Column('audience', json_type, nullable=False),
        sa.Column('context', json_type, server_default=sa.text("'{}'"), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cron', sa.String(128), nullable=True),
        sa.Column('created_by', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_campaigns'),
        schema=SCHEMA,
    )

    op.create_table(
        'campaign_runs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('campaign_id', sa.Uuid(), nullable=False),
        sa.Column('period_key', sa.String(64), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_campaign_runs'),
        sa.ForeignKeyConstraint(
            ['campaign_id'], [f'{SCHEMA}.campaigns.id'],
            name='fk_campaign_runs_campaign_id_campaigns', ondelete='CASCADE',
        ),
        # Уникальность запуска за период — защита от повторов после простоя
        # генератора: он не разошлёт прошлую пятницу второй раз.
        sa.UniqueConstraint('campaign_id', 'period_key', name='uq_campaign_runs_campaign_id_period_key'),
        schema=SCHEMA,
    )

    op.create_table(
        'short_links',
        sa.Column('key', sa.String(16), nullable=False),
        sa.Column('target_url', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('visits', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('purpose', sa.String(32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('key', name='pk_short_links'),
        schema=SCHEMA,
    )

    _seed_templates()


def _seed_templates() -> None:
    """Заводит стартовые шаблоны и их первые версии."""
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
    import uuid

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
    for table in (
        'short_links', 'campaign_runs', 'campaigns', 'deliveries',
        'notifications', 'subscriptions', 'template_versions', 'templates', 'events',
    ):
        op.drop_table(table, schema=SCHEMA)
