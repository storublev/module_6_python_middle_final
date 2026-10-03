"""Брокер уведомлений: точки обмена, очереди и отложенный повтор.

Схема (ADR-9 в docs/architecture/notifications.md):

* `notify` — точка обмена типа topic, куда публикуют **события** внешние
  сервисы кинотеатра. Ключ маршрутизации — `[сущность]-reporting.v1.[событие]`;
  очередь `notify.plan` привязана к ней по `#`, потому что планировщику
  интересны все события. Имя получателя в ключе не указывается: событие
  описывает факт, а кто им воспользуется — не его дело;
* `notify.internal` — точка обмена типа direct для этапов конвейера:
  `plan`, `render`, `send`, `dead`;
* `notify.dlx` — **Dead Letter Exchange**. Очереди этапов ссылаются на него,
  а очереди повтора `notify.<этап>.retry` держат сообщение заданное время и
  дают ему умереть обратно в `notify.internal` с ключом своего этапа.

Зачем повтор именно так. Прямой цикл «не получилось — пробуем снова» на
временной ошибке внешнего сервиса останавливает **всю** очередь: об этом
прямо предупреждает задача урока про RabbitMQ. Сообщение вместо этого уходит
в сторону и возвращается позже, а очередь продолжает разбираться.

Библиотека — `aio-pika`, а не `pika` из урока: воркер асинхронный (asyncpg,
httpx, aiosmtplib), и блокирующий клиент внутри event loop остановил бы всё
остальное. Протокол и брокер те же самые, меняется только клиент.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage, AbstractRobustConnection

from core.request_id import HEADER as REQUEST_ID_HEADER
from storage.base import MessagePublisher, StorageUnavailableError

logger = logging.getLogger(__name__)

EXCHANGE = 'notify'
INTERNAL_EXCHANGE = 'notify.internal'
DLX_EXCHANGE = 'notify.dlx'

STAGE_PLAN = 'plan'
STAGE_RENDER = 'render'
STAGE_SEND = 'send'
STAGE_DEAD = 'dead'
STAGES = (STAGE_PLAN, STAGE_RENDER, STAGE_SEND)

# Заголовок с номером попытки: по нему видно, когда пора сдаться и отправить
# сообщение в очередь разбора.
ATTEMPT_HEADER = 'x-attempt'


def queue_name(stage: str) -> str:
    """Имя очереди этапа по правилу урока: [микросервис].[действие-консьюмера]."""
    return f'notify.{stage}'


def retry_queue_name(stage: str) -> str:
    return f'notify.{stage}.retry'


class RabbitTopology:
    """Объявление точек обмена и очередей.

    Объявляется идемпотентно и каждым процессом при запуске: так стенд
    поднимается в любом порядке, а не «сначала API, потом воркеры».
    """

    def __init__(self, retry_delay_ms: int) -> None:
        self._retry_delay_ms = retry_delay_ms

    async def declare(self, channel: AbstractChannel) -> None:
        events = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        internal = await channel.declare_exchange(INTERNAL_EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True)
        dlx = await channel.declare_exchange(DLX_EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True)

        for stage in STAGES:
            # durable=True обязателен: без него очередь исчезнет вместе с
            # перезапуском брокера, а с ней и все принятые события.
            queue = await channel.declare_queue(
                queue_name(stage),
                durable=True,
                arguments={'x-dead-letter-exchange': DLX_EXCHANGE, 'x-dead-letter-routing-key': stage},
            )
            await queue.bind(internal, routing_key=stage)

            retry = await channel.declare_queue(
                retry_queue_name(stage),
                durable=True,
                arguments={
                    # Полежав здесь, сообщение «умирает» обратно в конвейер —
                    # это и есть отложенный повтор средствами самого брокера.
                    'x-message-ttl': self._retry_delay_ms,
                    'x-dead-letter-exchange': INTERNAL_EXCHANGE,
                    'x-dead-letter-routing-key': stage,
                },
            )
            await retry.bind(dlx, routing_key=stage)

        # Планировщик слушает ещё и события извне: все ключи вида
        # `*-reporting.v1.*` ведут в ту же очередь, разбирать их — его работа.
        plan_queue = await channel.get_queue(queue_name(STAGE_PLAN))
        await plan_queue.bind(events, routing_key='#')

        dead = await channel.declare_queue(queue_name(STAGE_DEAD), durable=True)
        await dead.bind(internal, routing_key=STAGE_DEAD)


async def connect(
    url: str, prefetch: int, retry_delay_ms: int,
) -> tuple[AbstractRobustConnection, AbstractChannel]:
    """Открывает устойчивое соединение и объявляет схему очередей.

    Канал аннотирован базовым `AbstractChannel`, а не «устойчивым» подтипом:
    так его объявляет сама библиотека, и код одинаково собирается на всех
    поддерживаемых версиях. Устойчивость даёт соединение — при обрыве оно
    переоткрывает канал само, и ничего специфичного от подтипа нам не нужно.
    """
    try:
        connection = await aio_pika.connect_robust(url)
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=prefetch)
        await RabbitTopology(retry_delay_ms).declare(channel)
    except Exception as error:  # noqa: BLE001 - наружу выходит контракт хранилища
        raise StorageUnavailableError(f'RabbitMQ недоступен: {error}') from error
    return connection, channel


class RabbitPublisher(MessagePublisher):
    """Публикация сообщений с подтверждением и записью на диск."""

    def __init__(self, channel: AbstractChannel, timeout: float) -> None:
        self._channel = channel
        # Без таймаута публикация при лежащем брокере не падает, а ждёт, пока
        # устойчивое соединение переподключится, — сколько угодно долго.
        # Ретранслятор outbox тогда висит вместо того, чтобы отложить задание.
        self._timeout = timeout

    async def publish(self, stage: str, payload: dict[str, Any], request_id: str) -> None:
        message = aio_pika.Message(
            body=_encode(payload),
            content_type='application/json',
            # delivery_mode=2: брокер сохраняет сообщение на диск, иначе оно
            # исчезнет вместе с его перезапуском.
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={REQUEST_ID_HEADER: request_id},
        )
        try:
            # Таймаут — на всю публикацию, а не только на `basic_publish`: свой
            # таймаут aio-pika начинает отсчитывать уже после того, как
            # устойчивый канал дождался переподключения, а это ожидание само
            # по себе не ограничено. Проверено остановкой брокера.
            await asyncio.wait_for(self._publish(stage, message), timeout=self._timeout)
        except Exception as error:  # noqa: BLE001 - наружу выходит контракт хранилища
            raise StorageUnavailableError(f'Не удалось опубликовать сообщение: {error!r}') from error

    async def _publish(self, stage: str, message: aio_pika.Message) -> None:
        exchange = await self._channel.get_exchange(INTERNAL_EXCHANGE)
        # publish с подтверждением: не идём дальше, пока брокер не принял
        # сообщение. Иначе API ответит «принято» на то, чего в очереди нет.
        await exchange.publish(message, routing_key=stage)


def _attempt_of(message: AbstractIncomingMessage) -> int:
    """Номер попытки из заголовка сообщения.

    Заголовки AMQP хранят значения кучей разных типов, и брокер вправе
    вернуть номер строкой. Всё, что не приводится к целому, считаем первой
    попыткой: потерять счётчик безопаснее, чем уронить обработку.
    """
    raw = (message.headers or {}).get(ATTEMPT_HEADER, 0)
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _encode(payload: dict[str, Any]) -> bytes:
    import json

    return json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')


class RabbitConsumer:
    """Чтение очереди этапа с отложенным повтором и очередью разбора."""

    def __init__(self, channel: AbstractChannel, stage: str, max_attempts: int) -> None:
        self._channel = channel
        self._stage = stage
        self._max_attempts = max_attempts

    async def consume(self, handler: Callable[[dict[str, Any], str], Awaitable[None]]) -> None:
        """Обрабатывает сообщения очереди, пока процесс жив.

        Успешная обработка подтверждает сообщение, неудачная — отправляет его
        в очередь повтора или, если попытки кончились, в очередь разбора.
        Сообщение не теряется ни в одном из случаев (НФТ-3).
        """
        queue = await self._channel.get_queue(queue_name(self._stage))
        async with queue.iterator() as messages:
            async for message in messages:
                await self._handle_one(message, handler)

    async def _handle_one(
        self, message: AbstractIncomingMessage, handler: Callable[[dict[str, Any], str], Awaitable[None]],
    ) -> None:
        import json

        request_id = str(message.headers.get(REQUEST_ID_HEADER, '-')) if message.headers else '-'
        attempt = _attempt_of(message)
        try:
            payload = json.loads(message.body)
        except ValueError:
            # Неразбираемое сообщение повторять бессмысленно — оно не станет
            # правильным от ожидания. Сразу в очередь разбора.
            logger.error('Сообщение %s не разбирается, отправлено в очередь разбора', self._stage)
            await self._park(message, attempt, to_dead=True)
            return

        try:
            await handler(payload, request_id)
        except Exception as error:  # noqa: BLE001 - причина уходит в журнал, сообщение не теряется
            logger.warning(
                'Этап %s не справился с сообщением (попытка %s): %s', self._stage, attempt + 1, error,
                extra={'stage': self._stage, 'attempt': attempt + 1},
            )
            await self._park(message, attempt, to_dead=attempt + 1 >= self._max_attempts)
            return
        await message.ack()

    async def _park(self, message: AbstractIncomingMessage, attempt: int, to_dead: bool) -> None:
        """Отправляет сообщение в очередь повтора или разбора и подтверждает исходное."""
        headers = dict(message.headers or {})
        headers[ATTEMPT_HEADER] = attempt + 1
        copy = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers,
        )
        if to_dead:
            exchange = await self._channel.get_exchange(INTERNAL_EXCHANGE)
            await exchange.publish(copy, routing_key=STAGE_DEAD)
        else:
            # Публикуем прямо в очередь повтора: её TTL вернёт сообщение на
            # этот же этап через заданное время.
            await self._channel.default_exchange.publish(copy, routing_key=retry_queue_name(self._stage))
        # Исходное подтверждаем только после того, как копия принята брокером:
        # иначе при сбое между этими шагами сообщение исчезло бы.
        await message.ack()
