"""Отклонённые события outbox: посмотреть и вернуть в отправку после исправления.

Сервис уведомлений отверг событие по существу (4xx) — ретранслятор отложил его
с причиной отказа и больше не отправляет. Когда причину исправили (добавили
шаблон, согласовали формат), события возвращают в очередь — с тем же
event_id, поэтому сервис уведомлений не задвоит письмо, если какое-то событие
всё-таки дошло до него в первый раз.

    docker compose exec booking-relay python outbox_cli.py list
    docker compose exec booking-relay python outbox_cli.py requeue --all
    docker compose exec booking-relay python outbox_cli.py requeue --id <event_id> [--id ...]
"""

import argparse
import asyncio
import json
import sys
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

import db.postgres as postgres
from core.config import settings
from services.relay import RejectedEvents
from storage.postgres import PostgresOutbox


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='отклонённые события с причиной отказа')
    listing.add_argument('--limit', type=int, default=100)
    requeue = commands.add_parser('requeue', help='вернуть отклонённые события в отправку')
    which = requeue.add_mutually_exclusive_group(required=True)
    which.add_argument('--all', action='store_true', help='все отклонённые события')
    which.add_argument('--id', dest='ids', type=UUID, action='append', help='event_id; можно несколько раз')
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    postgres.engine = postgres.create_engine(settings)
    try:
        async with async_sessionmaker(postgres.engine, expire_on_commit=False)() as session:
            events = RejectedEvents(PostgresOutbox(session))
            if args.command == 'list':
                for event in await events.list(args.limit):
                    print(json.dumps({
                        'event_id': str(event.id),
                        'rejected_at': event.rejected_at.isoformat(),
                        'attempts': event.attempts,
                        'error': event.last_error,
                        'template_code': event.payload.get('template_code'),
                    }, ensure_ascii=False))
                return 0
            requeued = await events.requeue(None if args.all else args.ids)
            print(f'Возвращено в отправку: {requeued}')
            return 0
    finally:
        await postgres.engine.dispose()


if __name__ == '__main__':
    sys.exit(asyncio.run(run(parse_args())))
