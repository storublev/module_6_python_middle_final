"""События для сервиса уведомлений: кому и о чём написать после брони.

Событие описывает факт («гость забронировал два места»), а не письмо: текст
лежит в шаблоне сервиса уведомлений, сюда попадают только данные для него.
Адрес и имя получателя сервис уведомлений берёт сам по `user_id` — в событии
их нет (гибридная схема модуля 5).

События кладутся в outbox той же транзакцией, что и бронь (ADR-23).
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from core.request_id import get_request_id
from models.domain import Booking, OutboxDraft, Screening

# Ключи маршрутизации по правилу сервиса уведомлений: [сущность]-reporting.[версия].[событие].
BOOKING_CREATED = 'booking-reporting.v1.created'
BOOKING_CHANGED = 'booking-reporting.v1.changed'
BOOKING_CANCELLED = 'booking-reporting.v1.cancelled'
SCREENING_CHANGED = 'screening-reporting.v1.changed'
SCREENING_CANCELLED = 'screening-reporting.v1.cancelled'

# Шаблоны писем — заводятся миграцией сервиса уведомлений.
TEMPLATE_GUEST_BOOKING = 'booking_confirmed'
TEMPLATE_HOST_BOOKING = 'booking_host_update'
TEMPLATE_SCREENING_CHANGED = 'screening_changed'
TEMPLATE_SCREENING_CANCELLED = 'screening_cancelled'

# Что случилось с бронью — для письма хосту одним шаблоном.
CHANGE_CREATED = 'created'
CHANGE_CHANGED = 'changed'
CHANGE_CANCELLED = 'cancelled'


class Letters:
    """Собирает события о бронях и показах."""

    def __init__(self, public_base_url: str, timezone: str) -> None:
        self._base_url = public_base_url.rstrip('/')
        self._zone = ZoneInfo(timezone)

    def booking_created(self, screening: Screening, booking: Booking) -> list[OutboxDraft]:
        return [
            self._event(BOOKING_CREATED, TEMPLATE_GUEST_BOOKING, [booking.guest_id], screening, booking,
                        CHANGE_CREATED),
            self._event(BOOKING_CREATED, TEMPLATE_HOST_BOOKING, [screening.host_id], screening, booking,
                        CHANGE_CREATED),
        ]

    def booking_changed(self, screening: Screening, booking: Booking) -> list[OutboxDraft]:
        return [
            self._event(BOOKING_CHANGED, TEMPLATE_GUEST_BOOKING, [booking.guest_id], screening, booking,
                        CHANGE_CHANGED),
            self._event(BOOKING_CHANGED, TEMPLATE_HOST_BOOKING, [screening.host_id], screening, booking,
                        CHANGE_CHANGED),
        ]

    def booking_cancelled(self, screening: Screening, booking: Booking) -> list[OutboxDraft]:
        # Гость отменил сам — ему писать незачем, он знает. Хосту — обязательно:
        # освободились места.
        return [
            self._event(BOOKING_CANCELLED, TEMPLATE_HOST_BOOKING, [screening.host_id], screening, booking,
                        CHANGE_CANCELLED),
        ]

    def screening_changed(self, screening: Screening, guest_ids: Iterable[UUID]) -> list[OutboxDraft]:
        return self._to_guests(SCREENING_CHANGED, TEMPLATE_SCREENING_CHANGED, screening, guest_ids)

    def screening_cancelled(self, screening: Screening, guest_ids: Iterable[UUID]) -> list[OutboxDraft]:
        return self._to_guests(SCREENING_CANCELLED, TEMPLATE_SCREENING_CANCELLED, screening, guest_ids)

    def _to_guests(
        self, routing_key: str, template: str, screening: Screening, guest_ids: Iterable[UUID],
    ) -> list[OutboxDraft]:
        recipients = sorted(set(guest_ids))
        if not recipients:
            return []
        # Одно событие на всех гостей: сервис уведомлений сам режет адресатов
        # на пачки, а показ камерный — гостей не больше пятидесяти.
        return [self._event(routing_key, template, recipients, screening)]

    def _event(
        self,
        routing_key: str,
        template: str,
        user_ids: list[UUID],
        screening: Screening,
        booking: Booking | None = None,
        change: str | None = None,
    ) -> OutboxDraft:
        context: dict[str, Any] = {
            'film_title': screening.film_title,
            'host_name': screening.host_name,
            'starts_at': self.local_time(screening.starts_at),
            'place': screening.place,
            'address': screening.address,
            'seats_left': screening.seats_left,
            'action_url': f'{self._base_url}/screenings/{screening.id}',
        }
        if booking is not None:
            context |= {'guest_name': booking.guest_name, 'seats': booking.seats, 'change': change or ''}
        payload = {
            'routing_key': routing_key,
            'template_code': template,
            'channel': 'email',
            'urgency': 'instant',
            'audience': {'kind': 'users', 'user_ids': [str(user_id) for user_id in user_ids]},
            'context': context,
        }
        return OutboxDraft(payload=payload, request_id=get_request_id())

    def local_time(self, moment: datetime) -> str:
        """Время показа так, как его ждёт увидеть человек: по местному времени и с поясом."""
        local = moment.astimezone(self._zone)
        return f'{local:%d.%m.%Y %H:%M} ({local.tzname()})'
