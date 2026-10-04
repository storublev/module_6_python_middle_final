"""Исследование: как не продать больше мест, чем есть, под одновременными бронями.

Сценарий — популярный хост публикует показ на 10 мест, и за секунды на него
приходят 200 гостей, каждый за одним-двумя местами. Каждая стратегия
прогоняется несколько раз на настоящем PostgreSQL; меряется, сколько мест
продано (больше 10 — перебронирование), сколько броней прошло, сколько
запросов пришлось повторить и сколько длился запрос гостя.

Стратегии:

* `read_check` — прочитать остаток, проверить в коде, записать бронь и новый
  счётчик (READ COMMITTED). Так пишут «по-простому»;
* `count_check` — посчитать занятые места суммой по броням, проверить,
  вставить бронь (READ COMMITTED);
* `serializable` — то же, что `count_check`, но на уровне SERIALIZABLE с
  повтором при ошибке сериализации (40001);
* `for_update` — заблокировать строку показа `SELECT … FOR UPDATE`,
  проверить, обновить счётчик, вставить бронь;
* `conditional` — один условный `UPDATE … WHERE seats_taken + n <= capacity
  RETURNING`, затем бронь (выбрано в сервисе, ADR-21).

Запуск (нужен пустой PostgreSQL; схема `research` создаётся и удаляется сама):

    python research/overbooking.py --dsn postgresql://booking:booking@127.0.0.1:55432/booking
"""

import argparse
import asyncio
import random
import statistics
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import asyncpg

SCHEMA = """
DROP SCHEMA IF EXISTS research CASCADE;
CREATE SCHEMA research;
CREATE TABLE research.screenings (
    id UUID PRIMARY KEY,
    capacity SMALLINT NOT NULL,
    seats_taken SMALLINT NOT NULL DEFAULT 0
);
CREATE TABLE research.bookings (
    id UUID PRIMARY KEY,
    screening_id UUID NOT NULL REFERENCES research.screenings (id),
    guest_id UUID NOT NULL,
    seats SMALLINT NOT NULL
);
"""
# CHECK намеренно не ставится: исследуется, держит ли гарантию сама стратегия.
# В сервисе он есть как последний рубеж.

SERIALIZATION_FAILURE = '40001'
MAX_RETRIES = 20


@dataclass
class Outcome:
    booked: int = 0
    refused: int = 0
    errors: int = 0
    retries: int = 0
    latencies: list[float] = field(default_factory=list)


Strategy = Callable[[asyncpg.Connection, uuid.UUID, int, Outcome], Awaitable[bool]]


async def insert_booking(conn: asyncpg.Connection, screening_id: uuid.UUID, seats: int) -> None:
    await conn.execute(
        'INSERT INTO research.bookings (id, screening_id, guest_id, seats) VALUES ($1, $2, $3, $4)',
        uuid.uuid4(), screening_id, uuid.uuid4(), seats,
    )


async def read_check(conn: asyncpg.Connection, screening_id: uuid.UUID, seats: int, _: Outcome) -> bool:
    async with conn.transaction():
        row = await conn.fetchrow(
            'SELECT capacity, seats_taken FROM research.screenings WHERE id = $1', screening_id,
        )
        if row['seats_taken'] + seats > row['capacity']:
            return False
        # Пауза имитирует работу приложения между чтением и записью (сеть,
        # проверки, имя гостя): ровно в это окно и проскакивают соперники.
        await asyncio.sleep(0.001)
        await conn.execute(
            'UPDATE research.screenings SET seats_taken = $2 WHERE id = $1', screening_id, row['seats_taken'] + seats,
        )
        await insert_booking(conn, screening_id, seats)
        return True


async def count_check(conn: asyncpg.Connection, screening_id: uuid.UUID, seats: int, _: Outcome) -> bool:
    async with conn.transaction():
        return await _count_and_insert(conn, screening_id, seats)


async def _count_and_insert(conn: asyncpg.Connection, screening_id: uuid.UUID, seats: int) -> bool:
    taken = await conn.fetchval(
        'SELECT COALESCE(SUM(seats), 0) FROM research.bookings WHERE screening_id = $1', screening_id,
    )
    capacity = await conn.fetchval('SELECT capacity FROM research.screenings WHERE id = $1', screening_id)
    if taken + seats > capacity:
        return False
    await asyncio.sleep(0.001)
    await insert_booking(conn, screening_id, seats)
    return True


async def serializable(conn: asyncpg.Connection, screening_id: uuid.UUID, seats: int, outcome: Outcome) -> bool:
    for _ in range(MAX_RETRIES):
        try:
            async with conn.transaction(isolation='serializable'):
                return await _count_and_insert(conn, screening_id, seats)
        except asyncpg.SerializationError:
            outcome.retries += 1
            # Пауза со случайным разбросом: без неё соперники сталкиваются снова.
            await asyncio.sleep(random.uniform(0, 0.01))  # noqa: S311 — не криптография
    raise RuntimeError('serialization retries exhausted')


async def for_update(conn: asyncpg.Connection, screening_id: uuid.UUID, seats: int, _: Outcome) -> bool:
    async with conn.transaction():
        row = await conn.fetchrow(
            'SELECT capacity, seats_taken FROM research.screenings WHERE id = $1 FOR UPDATE', screening_id,
        )
        if row['seats_taken'] + seats > row['capacity']:
            return False
        await asyncio.sleep(0.001)
        await conn.execute(
            'UPDATE research.screenings SET seats_taken = seats_taken + $2 WHERE id = $1', screening_id, seats,
        )
        await insert_booking(conn, screening_id, seats)
        return True


async def conditional(conn: asyncpg.Connection, screening_id: uuid.UUID, seats: int, _: Outcome) -> bool:
    async with conn.transaction():
        taken = await conn.fetchval(
            """
            UPDATE research.screenings SET seats_taken = seats_taken + $2
             WHERE id = $1 AND seats_taken + $2 <= capacity
            RETURNING seats_taken
            """,
            screening_id, seats,
        )
        if taken is None:
            return False
        await asyncio.sleep(0.001)
        await insert_booking(conn, screening_id, seats)
        return True


STRATEGIES: dict[str, Strategy] = {
    'read_check': read_check,
    'count_check': count_check,
    'serializable': serializable,
    'for_update': for_update,
    'conditional': conditional,
}


async def run_once(
    pool: asyncpg.Pool, strategy: Strategy, capacity: int, guests: int,
) -> tuple[Outcome, int, float]:
    screening_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO research.screenings (id, capacity) VALUES ($1, $2)', screening_id, capacity)
    outcome = Outcome()
    start = asyncio.Event()

    async def guest() -> None:
        seats = random.choice((1, 1, 2))  # noqa: S311 — не криптография
        await start.wait()
        began = time.perf_counter()
        try:
            async with pool.acquire() as conn:
                ok = await strategy(conn, screening_id, seats, outcome)
        except Exception:  # noqa: BLE001 — исследование считает любые отказы
            outcome.errors += 1
            return
        outcome.latencies.append((time.perf_counter() - began) * 1000)
        if ok:
            outcome.booked += 1
        else:
            outcome.refused += 1

    tasks = [asyncio.create_task(guest()) for _ in range(guests)]
    began = time.perf_counter()
    start.set()
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - began
    async with pool.acquire() as conn:
        sold = await conn.fetchval(
            'SELECT COALESCE(SUM(seats), 0) FROM research.bookings WHERE screening_id = $1', screening_id,
        )
    return outcome, sold, elapsed


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn', required=True)
    parser.add_argument('--capacity', type=int, default=10)
    parser.add_argument('--guests', type=int, default=200)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--pool', type=int, default=50, help='соединений с базой — как у нескольких воркеров API')
    args = parser.parse_args()

    pool = await asyncpg.create_pool(args.dsn, min_size=args.pool, max_size=args.pool)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    print(
        f'{args.guests} гостей одновременно на {args.capacity} мест, {args.runs} прогонов, '
        f'{args.pool} соединений\n',
    )
    header = f'{"стратегия":<13} {"продано мест":>13} {"перебронь":>9} {"броней":>7} {"отказов":>8} ' \
             f'{"ошибок":>7} {"повторов":>9} {"p50, мс":>8} {"p95, мс":>8} {"прогон, мс":>11}'
    print(header)
    print('-' * len(header))
    for name, strategy in STRATEGIES.items():
        sold, oversold, booked, refused, errors, retries, latencies, runs = [], 0, 0, 0, 0, 0, [], []
        for _ in range(args.runs):
            outcome, seats_sold, elapsed = await run_once(pool, strategy, args.capacity, args.guests)
            sold.append(seats_sold)
            oversold += seats_sold > args.capacity
            booked += outcome.booked
            refused += outcome.refused
            errors += outcome.errors
            retries += outcome.retries
            latencies += outcome.latencies
            runs.append(elapsed * 1000)
        quantiles = statistics.quantiles(latencies, n=20)
        print(
            f'{name:<13} {min(sold):>5}…{max(sold):<7} {oversold:>5}/{args.runs:<3} {booked / args.runs:>7.1f} '
            f'{refused / args.runs:>8.1f} {errors / args.runs:>7.1f} {retries / args.runs:>9.1f} '
            f'{statistics.median(latencies):>8.1f} {quantiles[18]:>8.1f} {statistics.mean(runs):>11.0f}',
        )
    async with pool.acquire() as conn:
        await conn.execute('DROP SCHEMA research CASCADE')
    await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
