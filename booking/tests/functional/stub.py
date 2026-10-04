"""Заглушка соседей сервиса бронирования: каталог фильмов и приём событий уведомлений.

Каталог и сервис уведомлений — большие системы (Elasticsearch, RabbitMQ,
воркеры), а от них бронированию нужно по одному эндпоинту. Их контракт
воспроизводится здесь дословно, а сервис авторизации в тестах настоящий: его
справочник имён — стык, который ломается чаще.

Заглушка запоминает принятые события (`GET /_events`) и умеет «лечь»
(`POST /_down`, `POST /_up`) — так тест проверяет, что outbox доносит
события после сбоя. Только стандартная библиотека: заглушке не нужен свой образ.
"""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MOVIE_ID = '3d825f60-9fff-4dfe-b294-1a45fa1e115d'
SERIES_ID = '0d6e2b3a-9a1c-4c7e-8b5f-3e2d1c0b9a87'
FILMS = {
    MOVIE_ID: {
        'uuid': MOVIE_ID, 'title': 'Star Wars', 'type': 'movie', 'imdb_rating': 8.6,
        'poster_url': 'https://m.media-amazon.com/images/M/sw._V1_QL75_UX400_.jpg',
    },
    SERIES_ID: {'uuid': SERIES_ID, 'title': 'Star Trek', 'type': 'tv_show', 'imdb_rating': 8.0, 'poster_url': None},
}
FILM_PATH = re.compile(r'^/api/v1/films/([0-9a-f-]{36})$')

state = {'events': [], 'down': False}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — имя задано http.server
        if self.path == '/_events':
            self._reply(200, state['events'])
            return
        match = FILM_PATH.match(self.path)
        if match and match.group(1) in FILMS:
            self._reply(200, FILMS[match.group(1)])
            return
        self._reply(404, {'detail': 'film not found'})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}')
        if self.path in ('/_down', '/_up'):
            state['down'] = self.path == '/_down'
            self._reply(200, {'down': state['down']})
        elif self.path == '/_reset':
            state['events'].clear()
            state['down'] = False
            self._reply(200, {})
        elif self.path == '/notify/api/v1/events':
            if state['down']:
                self._reply(503, {'code': 'service_unavailable', 'detail': 'down'})
                return
            if self.headers.get('X-Service-Token') != 'functional-tests-service-token':
                self._reply(401, {'code': 'service_token_invalid', 'detail': 'bad token'})
                return
            accepted = all(event['event_id'] != body['event_id'] for event in state['events'])
            if accepted:
                state['events'].append({**body, 'request_id': self.headers.get('X-Request-Id')})
            self._reply(202, {'event_id': body['event_id'], 'accepted': accepted})
        else:
            self._reply(404, {'detail': 'not found'})

    def _reply(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_: object) -> None:
        """Без журнала на каждый запрос: тесты шумят и так."""


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()  # noqa: S104 — внутри сети тестов
