"""История входов разбита на месячные секции.

Проверяется то, ради чего секции и заводились: запись попадает в секцию своего
месяца, чтение истории этого не замечает, а секции можно отцеплять и удалять
целиком, не трогая остальные данные.
"""

from datetime import UTC, datetime
from http import HTTPStatus
from uuid import uuid4

import pytest

from tests.functional.conftest import Account

TABLE = 'auth.login_history'
DEFAULT_PARTITION = 'login_history_default'


def partition_of(moment: datetime) -> str:
    return f'login_history_y{moment.year:04d}m{moment.month:02d}'


def month_after(moment: datetime, shift: int) -> datetime:
    """Середина месяца через `shift` месяцев: секции на них создаёт миграция.

    Даты в тестах считаются от текущего месяца, а не записаны числами: иначе
    тесты перестали бы работать, когда календарь уйдёт вперёд.
    """
    month = moment.month - 1 + shift
    return moment.replace(year=moment.year + month // 12, month=month % 12 + 1, day=15)


NOW = datetime.now(UTC)
NEXT_MONTH = month_after(NOW, 1)
IN_TWO_MONTHS = month_after(NOW, 2)


async def partition_counts(pg) -> dict[str, int]:
    """Сколько записей лежит в каждой секции."""
    rows = await pg.fetch(f'SELECT tableoid::regclass::text AS partition, count(*) FROM {TABLE} GROUP BY 1')
    return {row['partition'].split('.')[-1]: row['count'] for row in rows}


async def insert_login(pg, user_id: str, moment: datetime) -> None:
    """Записывает вход задним или будущим числом — через API так не сделать."""
    await pg.execute(
        f'INSERT INTO {TABLE} (id, created_at, user_id, user_agent, ip) VALUES ($1, $2, $3, $4, $5)',
        uuid4(), moment, user_id, 'pytest', '127.0.0.1',
    )


async def test_table_is_partitioned(pg):
    """Таблица истории входов секционирована, а не обычная."""
    partitioned = await pg.fetchval(
        "SELECT count(*) FROM pg_partitioned_table WHERE partrelid = 'auth.login_history'::regclass",
    )

    assert partitioned == 1


async def test_partitions_split_history_by_login_time(pg):
    """Секции делят историю по времени входа: таблица растёт именно вдоль этой оси."""
    key = await pg.fetchval("SELECT pg_get_partkeydef('auth.login_history'::regclass)")

    assert key == 'RANGE (created_at)'


async def test_default_partition_exists(pg):
    """Есть секция по умолчанию: вход в месяц без секции не потеряется и не сломает запись."""
    partitions = await pg.fetch(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'auth.login_history'::regclass",
    )

    assert DEFAULT_PARTITION in {row['relname'] for row in partitions}


async def test_login_goes_to_the_partition_of_its_month(client, neo: Account, pg):
    """Вход попадает в секцию текущего месяца."""
    await client.post('/login', json={'login': neo.login, 'password': neo.password})

    assert set(await partition_counts(pg)) == {partition_of(datetime.now(UTC))}


async def test_logins_of_different_months_go_to_different_partitions(neo: Account, pg):
    """Входы разных месяцев лежат в разных секциях — это и есть разбиение."""
    await insert_login(pg, neo.id, NEXT_MONTH)
    await insert_login(pg, neo.id, IN_TWO_MONTHS)

    counts = await partition_counts(pg)

    assert counts[partition_of(NEXT_MONTH)] == 1
    assert counts[partition_of(IN_TWO_MONTHS)] == 1


async def test_history_reads_across_partitions(client, neo: Account, pg):
    """Клиент разбиения не замечает: история читается целиком и от новых входов к старым."""
    await insert_login(pg, neo.id, NEXT_MONTH)
    await insert_login(pg, neo.id, IN_TWO_MONTHS)

    response = await client.get('/users/me/login-history', headers=neo.headers)

    assert response.status_code == HTTPStatus.OK
    moments = [record['created_at'] for record in response.json()]
    assert moments == sorted(moments, reverse=True)
    assert len(moments) == 3, 'вход при входе в фикстуре плюс два добавленных'


async def test_pagination_works_across_partitions(client, neo: Account, pg):
    """Страницы не дублируют и не теряют записи на границе секций."""
    for shift in (1, 2, 3):
        await insert_login(pg, neo.id, month_after(NOW, shift))

    first = await client.get('/users/me/login-history', params={'page_size': 2}, headers=neo.headers)
    second = await client.get(
        '/users/me/login-history', params={'page_size': 2, 'page_number': 2}, headers=neo.headers,
    )

    moments = [record['created_at'] for record in first.json() + second.json()]
    assert len(set(moments)) == 4


async def test_dropping_a_partition_removes_only_its_month(neo: Account, pg):
    """Месяц удаляется отсечением секции — ради этого секции и нужны.

    Секция возвращается на место: остальным тестам она ещё понадобится.
    """
    await insert_login(pg, neo.id, NEXT_MONTH)
    await insert_login(pg, neo.id, IN_TWO_MONTHS)
    dropped = partition_of(NEXT_MONTH)
    start = NEXT_MONTH.replace(day=1).date()
    end = IN_TWO_MONTHS.replace(day=1).date()

    await pg.execute(f'DROP TABLE auth.{dropped}')
    try:
        counts = await partition_counts(pg)
        assert dropped not in counts
        assert counts[partition_of(IN_TWO_MONTHS)] == 1
    finally:
        await pg.execute(
            f'CREATE TABLE auth.{dropped} PARTITION OF auth.login_history '
            f"FOR VALUES FROM ('{start}') TO ('{end}')",
        )


@pytest.mark.parametrize('shift', [0, 1, 2], ids=['текущий месяц', 'следующий', 'через два'])
async def test_prepared_partitions_exist(pg, shift):
    """Секции ближайших месяцев созданы заранее: вход не должен ждать обслуживания."""
    exists = await pg.fetchval('SELECT to_regclass($1)', f'auth.{partition_of(month_after(NOW, shift))}')

    assert exists is not None
