"""Клиенты внутренних API: каталог (Async API), бронирование и авторизация.

Методы возвращают JSON как есть — словари. Отдельные модели на стороне
интерфейса дублировали бы схемы API без пользы: шаблон и так читает поля по
имени, а контракт описан в OpenAPI каждого сервиса.
"""

from typing import Any
from uuid import UUID

from clients.base import ApiClient

Json = dict[str, Any]


class CatalogClient(ApiClient):
    name = 'Async API'

    async def films(
        self, page: int, size: int, genre: str | None = None, query: str | None = None, token: str | None = None,
    ) -> list[Json]:
        params: dict[str, Any] = {'page_number': page, 'page_size': size}
        if query:
            return await self._call('GET', '/api/v1/films/search', token, params=params | {'query': query})
        params['sort'] = '-imdb_rating'
        if genre:
            params['genre'] = genre
        return await self._call('GET', '/api/v1/films', token, params=params)

    async def film(self, film_id: UUID, token: str | None = None) -> Json:
        return await self._call('GET', f'/api/v1/films/{film_id}', token)

    async def genres(self) -> list[Json]:
        return await self._call('GET', '/api/v1/genres', params={'page_size': 100})

    async def person(self, person_id: UUID) -> Json:
        return await self._call('GET', f'/api/v1/persons/{person_id}')

    async def person_films(self, person_id: UUID, token: str | None = None) -> list[Json]:
        return await self._call('GET', f'/api/v1/persons/{person_id}/film', token, params={'page_size': 100})


class BookingClient(ApiClient):
    name = 'Сервис бронирования'
    PREFIX = '/booking/api/v1'

    async def hosts(self, film_id: UUID) -> Json:
        return await self._call('GET', f'{self.PREFIX}/films/{film_id}/hosts', params={'page_size': 50})

    async def screenings(
        self, film_id: UUID | None = None, host_id: UUID | None = None, page: int = 1, size: int = 50,
    ) -> Json:
        params: dict[str, Any] = {'page_number': page, 'page_size': size}
        if film_id:
            params['film_id'] = str(film_id)
        if host_id:
            params['host_id'] = str(host_id)
        return await self._call('GET', f'{self.PREFIX}/screenings', params=params)

    async def screening(self, screening_id: UUID) -> Json:
        return await self._call('GET', f'{self.PREFIX}/screenings/{screening_id}')

    async def create(self, token: str, body: Json) -> Json:
        return await self._call('POST', f'{self.PREFIX}/screenings', token, json=body)

    async def update(self, token: str, screening_id: UUID, body: Json) -> Json:
        return await self._call('PATCH', f'{self.PREFIX}/screenings/{screening_id}', token, json=body)

    async def cancel(self, token: str, screening_id: UUID) -> Json:
        return await self._call('POST', f'{self.PREFIX}/screenings/{screening_id}/cancel', token)

    async def guests(self, token: str, screening_id: UUID) -> list[Json]:
        return await self._call('GET', f'{self.PREFIX}/screenings/{screening_id}/bookings', token)

    async def my_booking(self, token: str, screening_id: UUID) -> Json:
        return await self._call('GET', f'{self.PREFIX}/screenings/{screening_id}/bookings/mine', token)

    async def book(self, token: str, screening_id: UUID, seats: int) -> Json:
        return await self._call(
            'POST', f'{self.PREFIX}/screenings/{screening_id}/bookings', token, json={'seats': seats},
        )

    async def change_seats(self, token: str, booking_id: UUID, seats: int) -> Json:
        return await self._call('PATCH', f'{self.PREFIX}/bookings/{booking_id}', token, json={'seats': seats})

    async def cancel_booking(self, token: str, booking_id: UUID) -> Json:
        return await self._call('POST', f'{self.PREFIX}/bookings/{booking_id}/cancel', token)

    async def rate(self, token: str, screening_id: UUID, target_id: UUID, score: int, comment: str | None) -> Json:
        body = {'target_id': str(target_id), 'score': score, 'comment': comment or None}
        return await self._call('POST', f'{self.PREFIX}/screenings/{screening_id}/ratings', token, json=body)

    async def my_ratings(self, token: str, screening_id: UUID) -> list[Json]:
        return await self._call('GET', f'{self.PREFIX}/screenings/{screening_id}/ratings/mine', token)

    async def my_screenings(self, token: str, period: str) -> Json:
        params = {'period': period, 'page_size': 50}
        return await self._call('GET', f'{self.PREFIX}/me/screenings', token, params=params)

    async def my_bookings(self, token: str, period: str) -> Json:
        params = {'period': period, 'page_size': 50}
        return await self._call('GET', f'{self.PREFIX}/me/bookings', token, params=params)

    async def rating(self, user_id: UUID) -> Json:
        return await self._call('GET', f'{self.PREFIX}/users/{user_id}/rating')

    async def reviews(self, user_id: UUID, role: str) -> Json:
        return await self._call(
            'GET', f'{self.PREFIX}/users/{user_id}/reviews', params={'role': role, 'page_size': 20},
        )


class AuthClient(ApiClient):
    name = 'Сервис авторизации'
    PREFIX = '/auth/api/v1'

    async def signup(self, login: str, password: str) -> Json:
        return await self._call('POST', f'{self.PREFIX}/signup', json={'login': login, 'password': password})

    async def login(self, login: str, password: str) -> Json:
        return await self._call('POST', f'{self.PREFIX}/login', json={'login': login, 'password': password})

    async def refresh(self, refresh_token: str) -> Json:
        return await self._call('POST', f'{self.PREFIX}/token/refresh', json={'refresh_token': refresh_token})

    async def logout(self, token: str) -> None:
        await self._call('POST', f'{self.PREFIX}/logout', token)

    async def me(self, token: str) -> Json:
        return await self._call('GET', f'{self.PREFIX}/users/me', token)

    async def update_profile(self, token: str, changes: Json) -> Json:
        return await self._call('PATCH', f'{self.PREFIX}/users/me/profile', token, json=changes)
