"""Демо-данные для бронирования: хосты, показы и брони через публичные API.

Стенд после `docker compose up` пуст: каталог есть, а показов нет, и в
карточке фильма показывать нечего. Скрипт заводит хостов и гостей в сервисе
авторизации и создаёт показы самых рейтинговых полнометражных фильмов так же,
как это сделал бы зритель в интерфейсе, — через API, а не записью в базу.
Поэтому он заодно проверяет весь путь: токен, каталог, справочник имён, бронь.

Повторный запуск безопасен: занятые логины не регистрируются заново, а входят.
Зрителей восемь — сервис авторизации пускает не больше десяти регистраций с
одного адреса в час, и это ограничение скрипт не обходит.

    python scripts/seed_demo.py                       # стенд на http://localhost
    python scripts/seed_demo.py --base-url http://localhost:8080 --films 30
"""

import argparse
import random
import sys
import time
from datetime import UTC, datetime, timedelta

import requests

# Пароль демо-учёток стенда: он печатается для входа на защите, это не секрет.
PASSWORD = 'demo-password-2026'  # noqa: S105
HOSTS = [
    ('demo-neo', 'Нео', 'Андерсон'),
    ('demo-trinity', 'Тринити', 'Ноль'),
    ('demo-morpheus', 'Морфеус', 'Капитан'),
    ('demo-niobe', 'Ниоба', 'Логос'),
]
GUESTS = [(f'demo-guest-{n}', name, 'Зритель') for n, name in enumerate(
    ('Анна', 'Борис', 'Вера', 'Глеб'), start=1,
)]
PLACES = [
    ('Кинотеатр «Октябрь», зал 3', 'Москва, Новый Арбат, 24'),
    ('Антикафе «Циферблат»', 'Москва, Покровка, 12'),
    ('Домашний кинозал', 'Москва, Ленинский проспект, 70'),
    ('Кинотеатр «Пионер»', 'Москва, Кутузовский проспект, 21'),
    ('Летняя веранда «Гараж»', 'Москва, Крымский Вал, 9'),
]


class Api:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip('/')
        self.session = requests.Session()

    def call(self, method: str, path: str, token: str | None = None, **kwargs) -> requests.Response:
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        for _ in range(10):
            response = self.session.request(method, f'{self.base}{path}', headers=headers, timeout=15, **kwargs)
            # nginx пускает вход и регистрацию не чаще пяти раз в секунду —
            # скрипт ждёт, как ждал бы человек, а не пробивает предел.
            if response.status_code != 429 or int(response.headers.get('Retry-After', 60)) > 5:
                return response
            time.sleep(int(response.headers.get('Retry-After', 1)))
        return response

    def viewer(self, login: str, first_name: str, last_name: str) -> tuple[str, str]:
        """(user_id, access_token): регистрирует зрителя или входит, если он уже есть."""
        credentials = {'login': login, 'password': PASSWORD}
        signup = self.call('POST', '/auth/api/v1/signup', json=credentials)
        if signup.status_code not in (201, 409):
            signup.raise_for_status()
        token = self.call('POST', '/auth/api/v1/login', json=credentials)
        token.raise_for_status()
        access = token.json()['access_token']
        self.call(
            'PATCH', '/auth/api/v1/users/me/profile', access,
            json={'first_name': first_name, 'last_name': last_name, 'email': f'{login}@practix.local'},
        ).raise_for_status()
        user_id = self.call('GET', '/auth/api/v1/users/me', access).json()['id']
        return user_id, access


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', default='http://localhost')
    parser.add_argument('--films', type=int, default=20, help='на сколько фильмов завести показы')
    args = parser.parse_args()
    api = Api(args.base_url)
    rng = random.Random(2026)  # noqa: S311 — демо-данные, не криптография

    films = api.call('GET', '/api/v1/films', params={'type': 'movie', 'sort': '-imdb_rating',
                                                     'page_size': args.films})
    films.raise_for_status()
    hosts = [api.viewer(*host) for host in HOSTS]
    guests = [api.viewer(*guest) for guest in GUESTS]
    # Показы — вечером по Москве (16:00–19:00 UTC — это 19:00–22:00 МСК):
    # в кино с компанией ходят после работы, а не в час ночи.
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    created = booked = 0
    for number, film in enumerate(films.json()):
        for _host_id, token in rng.sample(hosts, k=1 + number % 3):
            place, address = rng.choice(PLACES)
            starts = today + timedelta(days=rng.randint(1, 20), hours=rng.choice((16, 17, 18)),
                                       minutes=rng.choice((0, 30)))
            body = {
                'film_id': film['uuid'], 'starts_at': starts.isoformat(), 'place': place, 'address': address,
                'capacity': rng.choice((4, 6, 8, 10)),
                'description': rng.choice((None, 'После фильма — обсуждение', 'Попкорн за счёт хоста')),
            }
            screening = api.call('POST', '/booking/api/v1/screenings', token, json=body)
            if screening.status_code != 201:
                print(f'Показ «{film["title"]}» не создан: {screening.status_code} {screening.text}', file=sys.stderr)
                continue
            created += 1
            for _guest_id, guest_token in rng.sample(guests, k=rng.randint(0, 3)):
                seats = rng.randint(1, 2)
                response = api.call(
                    'POST', f'/booking/api/v1/screenings/{screening.json()["id"]}/bookings', guest_token,
                    json={'seats': seats},
                )
                booked += response.status_code == 201
    print(f'Фильмов: {len(films.json())}, показов создано: {created}, броней: {booked}')
    print(f'Вход для демонстрации: логин {HOSTS[0][0]} или {GUESTS[0][0]}, пароль {PASSWORD}')
    print(f'Пример карточки: {args.base_url}/films/{films.json()[0]["uuid"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
