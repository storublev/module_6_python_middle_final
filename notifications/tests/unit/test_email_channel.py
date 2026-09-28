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
