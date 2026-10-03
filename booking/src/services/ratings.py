"""Оценки хоста и гостя — задание со звёздочкой (ФТ-17…ФТ-19, ADR-26).

Посиделки камерные, и доверие к незнакомым людям строится на репутации.
Правила, которые не дают превратить оценки в накрутку:

* оценивать можно только **после начала** показа — до встречи оценивать нечего;
* только **участников** этого показа: гость с активной бронью оценивает
  хоста, хост — гостей с активной бронью; посторонний не оценит никого;
* одна оценка на пару «автор — оцениваемый» за показ — это держит
  уникальный индекс, а не проверка в коде;
* рейтинг хранится готовым счётчиком и двигается той же транзакцией, что и
  оценка: на горячем пути (карточка фильма) он только читается.
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from models.domain import Page, PageRequest, Rating, RatingDraft, Role, ScreeningStatus, UserRating
from services.errors import (
    AlreadyRatedError,
    InvalidRatingTargetError,
    NotParticipantError,
    RatingTooEarlyError,
    ScreeningCancelledError,
    ScreeningNotFoundError,
)
from storage.base import AlreadyExistsError, BookingRepository, RatingRepository, ScreeningRepository, UnitOfWork

Clock = Callable[[], datetime]


class RatingService:
    def __init__(
        self,
        uow: UnitOfWork,
        screenings: ScreeningRepository,
        bookings: BookingRepository,
        ratings: RatingRepository,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._screenings = screenings
        self._bookings = bookings
        self._ratings = ratings
        self._clock = clock

    async def rate(
        self, author_id: UUID, screening_id: UUID, target_id: UUID, score: int, comment: str | None,
    ) -> Rating:
        """Оценивает участника показа.

        Raises:
            ScreeningNotFoundError: показа нет.
            ScreeningCancelledError: показ отменён — встречи не было.
            RatingTooEarlyError: показ ещё не начался.
            NotParticipantError: автор не хост и не гость этого показа.
            InvalidRatingTargetError: гость оценивает не хоста, хост — не своего гостя.
            AlreadyRatedError: эту пару на этом показе уже оценивали.
        """
        screening = await self._screenings.get(screening_id)
        if screening is None:
            raise ScreeningNotFoundError
        if screening.status is ScreeningStatus.CANCELLED:
            raise ScreeningCancelledError
        if screening.starts_at > self._clock():
            raise RatingTooEarlyError

        if author_id == screening.host_id:
            guest = await self._bookings.active_of(screening_id, target_id)
            if guest is None:
                raise InvalidRatingTargetError('Host can rate only guests of this screening')
            author_name, role = screening.host_name, Role.GUEST
        else:
            booking = await self._bookings.active_of(screening_id, author_id)
            if booking is None:
                raise NotParticipantError
            if target_id != screening.host_id:
                raise InvalidRatingTargetError('Guest can rate only the host of this screening')
            author_name, role = booking.guest_name, Role.HOST

        draft = RatingDraft(
            screening_id=screening_id,
            author_id=author_id,
            author_name=author_name,
            target_id=target_id,
            target_role=role,
            score=score,
            comment=comment,
        )
        try:
            async with self._uow.transaction():
                rating = await self._ratings.add(draft)
        except AlreadyExistsError as error:
            raise AlreadyRatedError from error
        return rating

    async def mine(self, author_id: UUID, screening_id: UUID) -> list[Rating]:
        """Кого автор уже оценил на показе — интерфейс прячет у них кнопку."""
        if await self._screenings.get(screening_id) is None:
            raise ScreeningNotFoundError
        return await self._ratings.by_author(screening_id, author_id)

    async def summary(self, user_id: UUID) -> UserRating:
        return await self._ratings.summary(user_id)

    async def received(self, user_id: UUID, role: Role, page: PageRequest) -> Page[Rating]:
        return await self._ratings.received(user_id, role, page)
