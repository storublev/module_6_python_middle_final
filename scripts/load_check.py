"""Проверка требования к диплому: ни один запрос не отвечает дольше 300 мс.

Скрипт гоняет параллельных «зрителей» по стенду через nginx — по тем же
адресам, что открывает человек: каталог, карточка фильма с хостами, карточка
с выбранным хостом, афиша, страница показа и API бронирования. Для каждого
адреса считаются p50, p95 и максимум; если p95 хоть одного адреса больше
предела, скрипт завершается с кодом 1.

Перед запуском нужны демо-данные: `python scripts/seed_demo.py`.

    python scripts/load_check.py                      # 20 зрителей, 30 секунд
    python scripts/load_check.py --users 50 --seconds 60 --limit-ms 300
"""

import argparse
import asyncio
import statistics
import sys
import time
from collections import defaultdict

import httpx


async def discover(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    """Адреса для проверки, собранные с живого стенда: фильм с хостами и его показ."""
    screenings = (await client.get('/booking/api/v1/screenings', params={'page_size': 1})).json()['items']
    if not screenings:
        raise SystemExit('На стенде нет показов: сначала python scripts/seed_demo.py')
    screening = screenings[0]
    film, host = screening['film_id'], screening['host_id']
    return [
        ('каталог', '/'),
        ('каталог, жанр и страница 2', '/?page=2'),
        ('поиск', '/?q=star'),
        ('карточка фильма', f'/films/{film}'),
        ('карточка с выбранным хостом', f'/films/{film}?host={host}'),
        ('страница показа', f'/screenings/{screening["id"]}'),
        ('афиша', '/afisha'),
        ('страница хоста', f'/hosts/{host}'),
        ('API: хосты фильма', f'/booking/api/v1/films/{film}/hosts'),
        ('API: даты хоста', f'/booking/api/v1/screenings?film_id={film}&host_id={host}'),
        ('API: фильм', f'/api/v1/films/{film}'),
    ]


async def user(client: httpx.AsyncClient, targets: list[tuple[str, str]], until: float, timings: dict,
               errors: dict) -> None:
    index = 0
    while time.perf_counter() < until:
        name, path = targets[index % len(targets)]
        index += 1
        began = time.perf_counter()
        try:
            response = await client.get(path)
        except httpx.HTTPError:
            errors[name] += 1
            continue
        timings[name].append((time.perf_counter() - began) * 1000)
        if response.status_code >= 500:
            errors[name] += 1


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', default='http://localhost')
    parser.add_argument('--users', type=int, default=20)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--limit-ms', type=float, default=300)
    args = parser.parse_args()

    limits = httpx.Limits(max_connections=args.users, max_keepalive_connections=args.users)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10, limits=limits) as client:
        targets = await discover(client)
        # Прогрев: первые запросы наполняют кеши и пулы соединений, их в замер не берём.
        for _, path in targets:
            await client.get(path)
        timings: dict[str, list[float]] = defaultdict(list)
        errors: dict[str, int] = defaultdict(int)
        until = time.perf_counter() + args.seconds
        await asyncio.gather(*(user(client, targets, until, timings, errors) for _ in range(args.users)))

    total = sum(len(values) for values in timings.values())
    print(f'{args.users} зрителей, {args.seconds:.0f} с, запросов: {total} ({total / args.seconds:.0f} в секунду)\n')
    print(f'{"адрес":<30} {"запросов":>9} {"p50, мс":>8} {"p95, мс":>8} {"макс, мс":>9} {"5xx":>5}')
    failed = False
    for name, _ in targets:
        values = timings[name]
        p95 = statistics.quantiles(values, n=20)[18] if len(values) >= 20 else max(values)
        mark = '' if p95 <= args.limit_ms and not errors[name] else '  ← превышен предел'
        failed |= bool(mark)
        print(f'{name:<30} {len(values):>9} {statistics.median(values):>8.1f} {p95:>8.1f} {max(values):>9.1f} '
              f'{errors[name]:>5}{mark}')
    print(f'\nПредел p95: {args.limit_ms:.0f} мс — {"превышен" if failed else "соблюдён"}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
