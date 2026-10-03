"""Почтовый канал: SMTP с пулом соединений и ограничением темпа.

Почему пул. Урок «Как посылать быстрее» разбирает ровно этот случай:
установка SMTP-соединения занимает около пяти секунд, аутентификация —
0,4 секунды, а сама отправка письма — меньше секунды. Если соединение
открывать на каждое письмо, всё время рассылки уходит на подключение.
Соединения переиспользуются, и отправка ускоряется в разы.

Почему ограничение темпа. Внешний почтовый сервер положить проще, чем свой
сервис: у обычного аккаунта Gmail предел 500 писем в сутки, у почтового
Exchange-аккаунта — 30 в минуту. Воркер сам себя придерживает, а не ждёт,
пока его забанят.

Письмо собирается `EmailMessage` из стандартной библиотеки: он сам кодирует
кириллицу в теме и теле. HTML кладётся через `add_alternative(subtype='html')`,
а не `set_content` — иначе зритель получит набор тегов вместо письма.
"""

import asyncio
import logging
from email.message import EmailMessage
from email.utils import parseaddr
from time import monotonic

import aiosmtplib

from channels.base import ChannelUnavailableError, DeliveryChannel, MessageRejectedError
from models.enums import Channel
from models.notification import RenderedMessage

logger = logging.getLogger(__name__)

# Коды SMTP 5xx — постоянный отказ (нет такого ящика), 4xx — временный.
PERMANENT_FROM = 500


class RateLimiter:
    """Простой ограничитель темпа: не чаще N отправок в секунду."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1 / per_second if per_second > 0 else 0.0
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            now = monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._interval
        if delay:
            await asyncio.sleep(delay)


class SmtpConnectionPool:
    """Пул SMTP-соединений.

    Соединение возвращается в пул после отправки; сломанное закрывается и
    заменяется новым при следующем обращении. Размер пула ограничен, потому
    что почтовые серверы считают одновременные соединения с одного адреса.
    """

    def __init__(
        self, host: str, port: int, use_tls: bool, user: str, password: str, size: int, timeout: float,
    ) -> None:
        self._host, self._port, self._use_tls = host, port, use_tls
        self._user, self._password = user, password
        self._timeout = timeout
        self._free: asyncio.LifoQueue[aiosmtplib.SMTP] = asyncio.LifoQueue()
        self._slots = asyncio.Semaphore(size)

    async def acquire(self) -> aiosmtplib.SMTP:
        """Отдаёт соединение из пула или открывает новое.

        Место в пуле занимается до подключения, а значит, и вернуть его
        обязан тот, кто занял, если подключиться не удалось. Иначе каждая
        неудачная попытка навсегда съедает место: при пуле из четырёх
        соединений четыре сбоя сервера — и все следующие письма ждут
        освобождения, которого не будет, даже когда сервер уже поднялся.
        """
        await self._slots.acquire()
        try:
            try:
                client = self._free.get_nowait()
            except asyncio.QueueEmpty:
                return await self._connect()
            if not client.is_connected:
                # Сервер мог закрыть простаивающее соединение сам — тогда просто
                # открываем новое вместо того, чтобы отдавать сломанное.
                client = await self._connect()
            return client
        except BaseException:
            # BaseException, а не Exception: отмена задачи (CancelledError) тоже
            # должна вернуть место, иначе остановка воркера посреди
            # подключения оставит пул меньше, чем он был.
            self._slots.release()
            raise

    def release(self, client: aiosmtplib.SMTP, broken: bool = False) -> None:
        if broken:
            # Не закрываем аккуратно: соединение уже в неизвестном состоянии,
            # и попытка QUIT по нему повиснет на таймауте.
            client.close()
        else:
            self._free.put_nowait(client)
        self._slots.release()

    async def _connect(self) -> aiosmtplib.SMTP:
        client = aiosmtplib.SMTP(
            hostname=self._host, port=self._port, use_tls=self._use_tls, timeout=self._timeout,
        )
        try:
            await client.connect()
            if self._user:
                await client.login(self._user, self._password)
        except BaseException as error:
            # Соединение могло успеть открыться, а вход — нет. Такое соединение
            # не годится и в пул не попадёт, поэтому закрываем его здесь, а не
            # оставляем висеть до таймаута сервера.
            client.close()
            if isinstance(error, (aiosmtplib.SMTPException, OSError)):
                raise ChannelUnavailableError(f'SMTP недоступен: {error}') from error
            raise
        return client

    async def close(self) -> None:
        while not self._free.empty():
            client = self._free.get_nowait()
            client.close()


class EmailChannel(DeliveryChannel):
    """Отправка писем по SMTP."""

    def __init__(self, pool: SmtpConnectionPool, sender: str, limiter: RateLimiter) -> None:
        self._pool = pool
        self._sender = sender
        self._limiter = limiter

    @property
    def channel(self) -> Channel:
        return Channel.EMAIL

    async def send(self, message: RenderedMessage) -> None:
        if not message.address:
            raise MessageRejectedError('У получателя нет адреса почты')
        await self._limiter.wait()
        mail = build_email(self._sender, message)
        client = await self._pool.acquire()
        broken = False
        try:
            await client.send_message(mail)
        except aiosmtplib.SMTPRecipientsRefused as error:
            raise MessageRejectedError(f'Адрес отклонён сервером: {error}') from error
        except aiosmtplib.SMTPResponseException as error:
            broken = True
            if error.code >= PERMANENT_FROM:
                raise MessageRejectedError(f'SMTP отказал навсегда: {error.code} {error.message}') from error
            raise ChannelUnavailableError(f'SMTP ответил {error.code}: {error.message}') from error
        except (aiosmtplib.SMTPException, OSError) as error:
            broken = True
            raise ChannelUnavailableError(f'Ошибка отправки: {error}') from error
        finally:
            self._pool.release(client, broken=broken)

    async def close(self) -> None:
        await self._pool.close()


def build_email(sender: str, message: RenderedMessage) -> EmailMessage:
    """Собирает письмо с заголовками по RFC 822."""
    mail = EmailMessage()
    mail['From'] = sender
    mail['To'] = message.address
    mail['Subject'] = message.subject
    # Постоянный идентификатор из ключа идемпотентности. Если сервер принял
    # письмо, а ответ потерялся, повтор уйдёт с тем же Message-ID, и почтовая
    # служба, склеивающая письма по нему, покажет одно, а не два.
    mail['Message-ID'] = f'<{message.idempotency_key}@{_domain_of(sender)}>'
    # Текстовая часть — для почтовых клиентов без HTML и для антиспама: письмо
    # из одного HTML чаще считают подозрительным.
    mail.set_content(strip_html(message.body))
    mail.add_alternative(message.body, subtype='html')
    return mail


def _domain_of(sender: str) -> str:
    """Домен адреса отправителя для Message-ID; запасной — если адрес без домена."""
    _, address = parseaddr(sender)
    return address.rpartition('@')[2] or 'practix.local'


def strip_html(html: str) -> str:
    """Грубый текстовый вариант письма: без тегов и лишних пробелов."""
    import re

    text = re.sub(r'<(script|style)[\s\S]*?</\1>', ' ', html, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>|</p>|</div>|</h\d>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


class LoggingChannel(DeliveryChannel):
    """Канал-заглушка: письмо уходит в журнал.

    Включается, когда почтовый сервер не задан. Нужен, чтобы стек поднимался и
    работал локально без SMTP, а разработчик видел, что именно ушло бы зрителю.
    """

    def __init__(self, channel: Channel = Channel.EMAIL) -> None:
        self._channel = channel

    @property
    def channel(self) -> Channel:
        return self._channel

    async def send(self, message: RenderedMessage) -> None:
        logger.info(
            'Письмо не отправлено — почтовый сервер не задан: %s → %s',
            message.subject, message.address,
            extra={'user_id': str(message.user_id), 'template': message.template_code},
        )
