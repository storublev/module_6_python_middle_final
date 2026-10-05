"""Интерфейсы хранилищ сервиса бронирования.

Бизнес-логика знает, что показы, брони и оценки где-то лежат, фильмы где-то
описаны, а письма куда-то уходят, — и не знает ни о PostgreSQL, ни о HTTP. В
unit-тестах на место этих интерфейсов встают реализации в памяти; конкретные
выбираются в одном месте — `api/dependencies.py` и точке входа ретранслятора.

**Транзакции.** Методы репозиториев ничего не фиксируют сами: изменения
копятся в единице работы (`UnitOfWork`) и фиксируются одним `commit()`.
Так бронь, счётчик мест и событие для писем попадают в базу вместе или не
попадают вовсе, а решает, где граница транзакции, сервис.

Контракт общий для всех реализаций: сбой хранилища выходит наружу как
`StorageUnavailableError`, а не как исключение драйвера. API отвечает на него 503.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import UUID

from models.domain import (
    Booking,
    BookingDraft,
    BookingView,
    Film,
    GuestEntry,
    HostOffer,
    OutboxDraft,
    OutboxMessage,
    Page,
    PageRequest,
    Period,
    Rating,
    RatingDraft,
    RejectedEvent,
    Role,
    Screening,
    ScreeningChanges,
    ScreeningDraft,
    UserRating,
)


class StorageUnavailableError(Exception):
    """Хранилище или внешний сервис недоступны: нет соединения, таймаут, 5xx."""


class EventRejectedError(Exception):
    """Сервис уведомлений отверг событие по существу (4xx): повтор ничего не изменит."""


class AlreadyExistsError(Exception):
    """Запись нарушает уникальность: вторая активная бронь, повторная оценка."""


class UnitOfWork(ABC):
    """Граница транзакции: всё, что сделано репозиториями, фиксируется вместе."""

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Фиксирует сделанное в блоке или откатывает его, если блок не дошёл до конца.

        Откат нужен и при ошибке бизнес-логики посреди блока: места уже
        заняты условным UPDATE, а бронь не записалась — счётчик должен
        вернуться.
        """
        try:
            yield
        except BaseException:
            await self.rollback()
            raise
        await self.commit()


class ScreeningRepository(ABC):
    @abstractmethod
    async def add(self, draft: ScreeningDraft) -> Screening: ...

    @abstractmethod
    async def get(self, screening_id: UUID, *, lock: bool = False) -> Screening | None:
        """Показ по id. `lock` — заблокировать строку до конца транзакции."""

    @abstractmethod
    async def update(self, screening_id: UUID, changes: ScreeningChanges) -> Screening | None:
        """Меняет показ. None — новое число мест меньше занятых, ничего не изменено.

        Проверка мест и запись — одно условие в базе, а не чтение и запись:
        между ними успела бы пройти чужая бронь.
        """

    @abstractmethod
    async def take_seats(self, screening_id: UUID, seats: int, now: datetime) -> Screening | None:
        """Занимает места, если показ открыт и мест хватает; иначе None.

        Главная гарантия задания (ФТ-12): условие «мест хватает» проверяется
        и выполняется атомарно, одновременные брони не продают лишнего.
        """

    @abstractmethod
    async def release_seats(self, screening_id: UUID, seats: int) -> None: ...

    @abstractmethod
    async def cancel(self, screening_id: UUID) -> None: ...

    @abstractmethod
    async def upcoming(
        self, now: datetime, page: PageRequest, film_id: UUID | None = None, host_id: UUID | None = None,
    ) -> Page[Screening]:
        """Будущие запланированные показы по времени начала."""

    @abstractmethod
    async def of_host(self, host_id: UUID, period: Period, now: datetime, page: PageRequest) -> Page[Screening]:
        """Расписание хоста, включая отменённые: хост должен видеть и их."""

    @abstractmethod
    async def hosts_of_film(self, film_id: UUID, now: datetime, page: PageRequest) -> Page[HostOffer]:
        """Хосты, у которых есть будущие показы фильма со свободными местами или без."""


class BookingRepository(ABC):
    @abstractmethod
    async def add(self, draft: BookingDraft) -> Booking:
        """Новая бронь.

        Raises:
            AlreadyExistsError: у гостя уже есть активная бронь на этот показ.
        """

    @abstractmethod
    async def get(self, booking_id: UUID, *, lock: bool = False) -> Booking | None: ...

    @abstractmethod
    async def active_of(self, screening_id: UUID, guest_id: UUID) -> Booking | None: ...

    @abstractmethod
    async def change_seats(self, booking_id: UUID, seats: int) -> Booking: ...

    @abstractmethod
    async def cancel(self, booking_id: UUID) -> Booking: ...

    @abstractmethod
    async def cancel_all(self, screening_id: UUID) -> list[Booking]:
        """Отменяет все активные брони показа и возвращает их — гостям надо написать."""

    @abstractmethod
    async def guests(self, screening_id: UUID) -> list[GuestEntry]:
        """Активные брони показа с рейтингом гостя как гостя."""

    @abstractmethod
    async def of_guest(self, guest_id: UUID, period: Period, now: datetime, page: PageRequest) -> Page[BookingView]: ...


class RatingRepository(ABC):
    @abstractmethod
    async def add(self, draft: RatingDraft) -> Rating:
        """Оценка и сдвиг агрегата оцениваемого — в одной транзакции.

        Raises:
            AlreadyExistsError: автор уже оценил этого участника на этом показе.
        """

    @abstractmethod
    async def by_author(self, screening_id: UUID, author_id: UUID) -> list[Rating]: ...

    @abstractmethod
    async def received(self, user_id: UUID, role: Role, page: PageRequest) -> Page[Rating]: ...

    @abstractmethod
    async def summary(self, user_id: UUID) -> UserRating: ...


class Outbox(ABC):
    @abstractmethod
    async def add(self, drafts: Sequence[OutboxDraft]) -> None:
        """Кладёт события в текущую транзакцию: они уйдут, только если она зафиксируется."""

    @abstractmethod
    async def claim(self, limit: int, lease: timedelta, now: datetime) -> list[OutboxMessage]:
        """Забирает события, которым пора, и откладывает их на время аренды. Отклонённые не берёт."""

    @abstractmethod
    async def done(self, message_id: UUID) -> None: ...

    @abstractmethod
    async def retry(self, message_id: UUID, at: datetime, error: str) -> None: ...

    @abstractmethod
    async def reject(self, message_id: UUID, at: datetime, error: str) -> None:
        """Откладывает событие, отвергнутое сервисом уведомлений, вместе с причиной — до исправления."""

    @abstractmethod
    async def rejected(self, limit: int) -> list[RejectedEvent]:
        """Отклонённые события, старые первыми."""

    @abstractmethod
    async def requeue(self, message_ids: Sequence[UUID] | None, now: datetime) -> int:
        """Возвращает отклонённые события в отправку (None — все). Возвращает их число.

        Идентификатор строки не меняется — это event_id, и сервис уведомлений
        узнает повтор, если событие всё-таки дошло до него в первый раз.
        """


class Catalog(ABC):
    """Каталог фильмов — Async API."""

    @abstractmethod
    async def film(self, film_id: UUID, authorization: str | None) -> Film | None:
        """Фильм по id или None, если в каталоге его нет.

        `authorization` — заголовок зрителя: подписочный фильм каталог отдаёт
        только тому, у кого есть подписка, и хост без неё такой показ не создаст.
        """


class People(ABC):
    """Справочник имён зрителей — сервис авторизации."""

    @abstractmethod
    async def names(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        """Имена для показа на страницах. Кого нет в справочнике — того нет в ответе."""


class Sessions(ABC):
    """Сессии входа — сервис авторизации."""

    @abstractmethod
    async def is_active(self, authorization: str) -> bool:
        """Жива ли сессия, к которой выпущен access-токен.

        False — сессия закрыта выходом, сменой пароля или «выйти на остальных
        устройствах», хотя подпись и срок токена ещё в порядке.

        Raises:
            StorageUnavailableError: сервис авторизации не ответил.
        """


class NotificationGateway(ABC):
    """Сервис уведомлений."""

    @abstractmethod
    async def send(self, event_id: UUID, payload: dict, request_id: str) -> None:
        """Отправляет событие. Повтор с тем же event_id письма не задваивает.

        Raises:
            StorageUnavailableError: сервис недоступен — стоит повторить позже.
            EventRejectedError: сервис отверг событие — повторять бессмысленно.
        """
