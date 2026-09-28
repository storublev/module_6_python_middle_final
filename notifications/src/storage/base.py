"""Интерфейсы хранилищ сервиса уведомлений.

Бизнес-логика знает, что события, шаблоны, подписки и отправки где-то лежат, а
сообщения куда-то публикуются, — и не знает ни о PostgreSQL, ни о RabbitMQ, ни
о HTTP. В unit-тестах на место этих интерфейсов встают реализации в памяти;
конкретные выбираются в одном месте — `api/dependencies.py` и точках входа
воркеров.

Контракт общий для всех реализаций: сбой хранилища выходит наружу как
`StorageUnavailableError`, а не как исключение драйвера. API отвечает на него
503, воркер — возвращает сообщение в очередь.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from models.campaign import Campaign, CampaignDraft
from models.enums import Channel, DeliveryStatus
from models.event import Event
from models.notification import (
    Delivery,
    NotificationRecord,
    Page,
    Recipient,
    RenderedMessage,
    ShortLink,
    Subscription,
    Template,
    TemplateDraft,
)


class StorageUnavailableError(Exception):
    """Хранилище недоступно: нет соединения, таймаут, отказ записи."""


class AlreadyExistsError(Exception):
    """Запись с таким уникальным ключом уже есть."""


class EventStore(ABC):
    """Принятые события. Нужен только ради идемпотентности приёма."""

    @abstractmethod
    async def remember(self, event: Event) -> bool:
        """Запоминает событие; False — такое `event_id` уже принимали.

        Повтор запроса после потерянного ответа не должен рождать второе
        уведомление (ФТ-2), поэтому решение принимается по уникальному ключу
        в базе, а не проверкой «нет ли такого» перед вставкой: между проверкой
        и вставкой успевает пройти соперник.
        """


class TemplateRepository(ABC):
    """Шаблоны писем. CRUD из админ-панели, чтение — воркером."""

    @abstractmethod
    async def get(self, code: str) -> Template | None:
        """Действующая версия шаблона или None."""

    @abstractmethod
    async def get_version(self, code: str, version: int) -> Template | None:
        """Конкретная версия: рассылка собирается той, с которой началась."""

    @abstractmethod
    async def list_all(self) -> list[Template]:
        """Все шаблоны по коду."""

    @abstractmethod
    async def create(self, draft: TemplateDraft) -> Template:
        """Создаёт шаблон.

        Raises:
            AlreadyExistsError: код занят.
        """

    @abstractmethod
    async def update(self, code: str, draft: TemplateDraft) -> Template | None:
        """Меняет шаблон и повышает его версию; None — шаблона нет."""

    @abstractmethod
    async def delete(self, code: str) -> bool:
        """Удаляет шаблон. False — его и не было."""


class SubscriptionRepository(ABC):
    """Подписки и настройки уведомлений зрителя."""

    @abstractmethod
    async def list_for_user(self, user_id: UUID) -> list[Subscription]:
        """Явно заданные настройки зрителя. Отсутствие записи означает согласие."""

    @abstractmethod
    async def set_enabled(self, user_id: UUID, template_code: str, channel: Channel, enabled: bool) -> Subscription:
        """Включает или выключает тип уведомлений."""

    @abstractmethod
    async def unsubscribe_all(self, user_id: UUID) -> None:
        """Отписывает зрителя от всего сразу.

        Выключает и уже заданные настройки, и ставит общий признак «не писать
        мне вовсе». Без общего признака отписка ничего не значила бы для
        зрителя, который никогда ничего не настраивал: выключать было бы
        нечего, а письма продолжали бы приходить.
        """

    @abstractmethod
    async def is_unsubscribed(self, user_ids: Sequence[UUID]) -> set[UUID]:
        """Кто из перечисленных отписан от всего."""

    @abstractmethod
    async def filter_enabled(
        self, user_ids: Sequence[UUID], template_code: str, channel: Channel,
    ) -> set[UUID]:
        """Оставляет тех, кому этот тип уведомлений слать можно.

        Проверка пачкой, а не по одному: планировщик разворачивает сегмент в
        тысячи получателей, и тысяча запросов в базу здесь была бы такой же
        ошибкой, как тысяча запросов в сервис авторизации.
        """


class NotificationRepository(ABC):
    """Уведомления о данных: о чём зрителю уже сообщали."""

    @abstractmethod
    async def filter_outdated(
        self, user_ids: Sequence[UUID], template_code: str, content_id: str, content_version: int | None,
    ) -> set[UUID]:
        """Оставляет тех, кому о **такой** версии данных ещё не писали (ФТ-6)."""

    @abstractmethod
    async def mark_notified(
        self, user_id: UUID, template_code: str, content_id: str, content_version: int | None, at: datetime,
    ) -> None:
        """Запоминает версию, о которой сообщили."""

    @abstractmethod
    async def get(self, user_id: UUID, template_code: str, content_id: str) -> NotificationRecord | None:
        """Запись уведомления или None."""


class DeliveryRepository(ABC):
    """История отправок и ключи идемпотентности."""

    @abstractmethod
    async def reserve(self, message: RenderedMessage) -> Delivery | None:
        """Занимает ключ идемпотентности до отправки.

        None означает, что ключ уже занят: письмо отправлено или отправляется
        прямо сейчас, и второй раз его слать не нужно. Это и есть защита от
        дублей поверх гарантии at-least-once (ADR-11).
        """

    @abstractmethod
    async def finish(self, idempotency_key: str, status: DeliveryStatus, error: str | None = None) -> None:
        """Проставляет итог отправки."""

    @abstractmethod
    async def release(self, idempotency_key: str) -> None:
        """Снимает бронь, чтобы сообщение можно было повторить.

        Нужен, когда отправка не состоялась по причине, которая пройдёт сама
        (почтовый сервер не ответил): без этого повтор упёрся бы в свой же
        ключ и письмо не ушло бы никогда.
        """

    @abstractmethod
    async def list_for_user(self, user_id: UUID, page_number: int, page_size: int) -> Page[Delivery]:
        """Последние уведомления зрителя для личного кабинета."""


class CampaignRepository(ABC):
    """Рассылки менеджера."""

    @abstractmethod
    async def create(self, draft: CampaignDraft, created_by: str | None) -> Campaign:
        """Создаёт рассылку."""

    @abstractmethod
    async def get(self, campaign_id: UUID) -> Campaign | None:
        """Рассылка или None."""

    @abstractmethod
    async def list_all(self) -> list[Campaign]:
        """Все рассылки, новые сверху."""

    @abstractmethod
    async def set_status(self, campaign_id: UUID, status: str) -> Campaign | None:
        """Меняет состояние рассылки."""

    @abstractmethod
    async def due(self, moment: datetime) -> list[Campaign]:
        """Рассылки, которым пора: наступило время разовой или срок повторяемой."""

    @abstractmethod
    async def claim_run(self, campaign_id: UUID, period_key: str, event_id: UUID) -> bool:
        """Отмечает запуск рассылки за период; False — его уже отмечали.

        Уникальный ключ `(campaign_id, period_key)` — защита от повторов после
        простоя генератора (НФТ-5).
        """

    @abstractmethod
    async def context_of(self, campaign_id: UUID) -> dict[str, Any]:
        """Данные, которые менеджер задал для подстановки в шаблон."""


class ShortLinkRepository(ABC):
    """Короткие ссылки из писем."""

    @abstractmethod
    async def create(
        self, target_url: str, user_id: UUID | None, expires_at: datetime | None, purpose: str | None,
    ) -> ShortLink:
        """Заводит короткую ссылку и возвращает её ключ."""

    @abstractmethod
    async def resolve(self, key: str, at: datetime) -> ShortLink | None:
        """Отдаёт ссылку и считает переход; None — ключа нет или срок вышел."""


class ContactDirectory(ABC):
    """Контакты зрителей: их знает сервис авторизации, а не мы."""

    @abstractmethod
    async def contacts(self, user_ids: Sequence[UUID]) -> list[Recipient]:
        """Контакты пачкой: на тысячу адресатов — один запрос, а не тысяча."""

    @abstractmethod
    async def page(self, after_id: UUID | None, limit: int) -> tuple[list[Recipient], UUID | None]:
        """Страница всех зрителей с почтой и ключ следующей страницы."""


class MessagePublisher(ABC):
    """Публикация сообщений в брокер."""

    @abstractmethod
    async def publish(self, stage: str, payload: dict[str, Any], request_id: str) -> None:
        """Кладёт сообщение на этап конвейера.

        `request_id` едет заголовком: без него в общем журнале не связать
        письмо с действием, которое его породило, — этого прямо требует урок
        про RabbitMQ.
        """
