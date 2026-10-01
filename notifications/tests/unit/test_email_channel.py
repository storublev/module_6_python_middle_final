"""Почтовый канал: сборка письма, ограничение темпа и пул соединений."""

import asyncio
from uuid import uuid4

import pytest

from channels.email import RateLimiter, build_email, strip_html
from models.enums import Channel
from models.notification import RenderedMessage

SENDER = 'Practix <noreply@practix.local>'


def message(**overrides: object) -> RenderedMessage:
    data: dict = {
        'idempotency_key': 'key-1',
        'user_id': uuid4(),
        'channel': Channel.EMAIL,
        'template_code': 'welcome',
        'address': 'viewer@example.com',
        'subject': 'Добро пожаловать в Practix!',
        'body': '<h1>Привет!</h1><p>Смотрите кино</p>',
    }
    data.update(overrides)
    return RenderedMessage(**data)


def test_email_has_required_headers() -> None:
    """У письма есть заголовки, по которым почтовый сервер его прочитает."""
    mail = build_email(SENDER, message())

    assert mail['From'] == SENDER
    assert mail['To'] == 'viewer@example.com'
    assert mail['Subject'] == 'Добро пожаловать в Practix!'


def test_message_id_is_stable_for_the_same_letter() -> None:
    """Повтор того же письма уходит с тем же Message-ID.

    Если сервер принял письмо, а ответ потерялся, повтор неизбежен: SMTP не
    даёт узнать, дошло ли письмо. Постоянный идентификатор позволяет почтовой
    службе склеить повтор с оригиналом.
    """
    first = build_email(SENDER, message(idempotency_key='abc123'))
    again = build_email(SENDER, message(idempotency_key='abc123'))
    other = build_email(SENDER, message(idempotency_key='def456'))

    assert first['Message-ID'] == again['Message-ID'] == '<abc123@practix.local>'
    assert other['Message-ID'] != first['Message-ID']


def test_cyrillic_subject_is_encoded() -> None:
    """Кириллица в теме кодируется, а не уезжает кракозябрами.

    Именно на этом обжёгся пример из урока: Coursera разослала россиянам
    письма в неверной кодировке.
    """
    mail = build_email(SENDER, message(subject='Вышла новая серия'))

    # Кодировка появляется при сериализации письма — именно в таком виде оно
    # уходит на сервер, и именно так выглядит вывод в уроке.
    raw = mail.as_string()
    assert 'Вышла новая серия' not in raw
    assert '=?utf-8?' in raw


def test_html_body_is_an_alternative_not_plain_text() -> None:
    """HTML кладётся через add_alternative, а не set_content.

    С `set_content` зритель получит набор тегов вместо письма — эту грабли
    урок называет прямо.
    """
    mail = build_email(SENDER, message())

    assert mail.is_multipart()
    subtypes = {part.get_content_subtype() for part in mail.iter_parts()}
    assert 'html' in subtypes
    assert 'plain' in subtypes


def test_plain_part_has_no_tags() -> None:
    """Текстовая часть письма — без разметки: её читают клиенты без HTML."""
    text = strip_html('<h1>Привет!</h1><p>Смотрите <b>кино</b></p>')

    assert '<' not in text
    assert 'Привет!' in text
    assert 'кино' in text


def test_script_content_is_not_leaked_into_plain_part() -> None:
    """Содержимое script не попадает в текстовую часть как текст."""
    text = strip_html('<p>Привет</p><script>alert("зловред")</script>')

    assert 'зловред' not in text


async def test_rate_limiter_spaces_out_sends() -> None:
    """Ограничитель темпа разносит отправки во времени.

    Внешний почтовый сервер положить проще, чем свой сервис: у обычного
    аккаунта Gmail предел 500 писем в сутки.
    """
    limiter = RateLimiter(per_second=50)
    loop = asyncio.get_running_loop()

    started = loop.time()
    for _ in range(3):
        await limiter.wait()
    elapsed = loop.time() - started

    # Три отправки при 50 в секунду — это минимум два интервала по 20 мс.
    assert elapsed >= 0.03


async def test_zero_rate_means_no_limit() -> None:
    """Нулевой темп снимает ограничение: на стенде ждать незачем."""
    limiter = RateLimiter(per_second=0)
    loop = asyncio.get_running_loop()

    started = loop.time()
    for _ in range(100):
        await limiter.wait()

    assert loop.time() - started < 0.05


def test_message_without_address_is_rejected() -> None:
    """Письмо без адреса не собирается: отправлять его некуда."""
    from channels.base import MessageRejectedError
    from channels.email import EmailChannel, SmtpConnectionPool

    channel = EmailChannel(
        SmtpConnectionPool('localhost', 25, False, '', '', 1, 5.0), SENDER, RateLimiter(0),
    )

    with pytest.raises(MessageRejectedError):
        asyncio.run(channel.send(message(address='')))


class FlakySmtp:
    """Подставной SMTP-клиент: подключение и вход ломаются по команде теста."""

    connect_fails = False
    login_fails = False
    connect_hangs = False
    instances: list['FlakySmtp'] = []

    def __init__(self, **_: object) -> None:
        self.is_connected = False
        self.closed = False
        FlakySmtp.instances.append(self)

    async def connect(self) -> None:
        if FlakySmtp.connect_hangs:
            await asyncio.sleep(3600)
        if FlakySmtp.connect_fails:
            raise ConnectionRefusedError('сервер не отвечает')
        self.is_connected = True

    async def login(self, user: str, password: str) -> None:
        if FlakySmtp.login_fails:
            import aiosmtplib

            raise aiosmtplib.SMTPAuthenticationError(535, 'неверный пароль')

    def close(self) -> None:
        self.closed = True
        self.is_connected = False


@pytest.fixture
def flaky_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FlakySmtp]:
    import channels.email

    FlakySmtp.connect_fails = FlakySmtp.login_fails = FlakySmtp.connect_hangs = False
    FlakySmtp.instances = []
    monkeypatch.setattr(channels.email.aiosmtplib, 'SMTP', FlakySmtp)
    return FlakySmtp


def pool(size: int = 2, user: str = '') -> 'SmtpConnectionPool':  # noqa: F821 - импорт внутри
    from channels.email import SmtpConnectionPool

    return SmtpConnectionPool('smtp.local', 25, False, user, 'secret', size, 5.0)


async def test_failed_connections_do_not_exhaust_the_pool(flaky_smtp: type[FlakySmtp]) -> None:
    """Сбои подключения не съедают места в пуле.

    Раньше место занималось до подключения и не возвращалось при ошибке:
    после стольких сбоев, сколько мест в пуле, все следующие письма ждали
    вечно, даже когда сервер уже поднялся.
    """
    from channels.base import ChannelUnavailableError

    smtp_pool = pool(size=2)
    flaky_smtp.connect_fails = True
    for _ in range(5):
        with pytest.raises(ChannelUnavailableError):
            await smtp_pool.acquire()

    flaky_smtp.connect_fails = False
    first = await asyncio.wait_for(smtp_pool.acquire(), timeout=1)
    second = await asyncio.wait_for(smtp_pool.acquire(), timeout=1)

    assert first.is_connected and second.is_connected


async def test_failed_login_closes_the_connection(flaky_smtp: type[FlakySmtp]) -> None:
    """Соединение, на котором не удался вход, закрывается и место возвращается."""
    from channels.base import ChannelUnavailableError

    smtp_pool = pool(size=1, user='mailer')
    flaky_smtp.login_fails = True

    with pytest.raises(ChannelUnavailableError):
        await smtp_pool.acquire()

    assert flaky_smtp.instances[0].closed is True
    flaky_smtp.login_fails = False
    client = await asyncio.wait_for(smtp_pool.acquire(), timeout=1)
    assert client.is_connected


async def test_cancelled_connection_returns_its_slot(flaky_smtp: type[FlakySmtp]) -> None:
    """Отмена посреди подключения тоже возвращает место: так останавливается воркер."""
    smtp_pool = pool(size=1)
    flaky_smtp.connect_hangs = True
    pending = asyncio.create_task(smtp_pool.acquire())
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    flaky_smtp.connect_hangs = False
    client = await asyncio.wait_for(smtp_pool.acquire(), timeout=1)
    assert client.is_connected
