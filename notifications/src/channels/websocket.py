"""Канал мгновенных сообщений в открытую вкладку.

Websocket-шлюз — отдельный процесс: он держит тысячи долгоживущих соединений,
и мешать это с обычным API нельзя. Соединение живёт там, а отправитель живёт
здесь, поэтому воркер не пишет в сокет сам, а просит шлюз доставить сообщение
служебным HTTP-запросом.

Если зритель сейчас не в сети, шлюз отвечает «некому»: сообщение не теряется,
оно уже записано в историю уведомлений и видно в личном кабинете. Повторять
такую отправку бессмысленно, поэтому это не сбой канала.
"""

import logging

import httpx

from channels.base import ChannelUnavailableError, DeliveryChannel
from core.request_id import HEADER as REQUEST_ID_HEADER
from core.request_id import get_request_id
from models.enums import Channel
from models.notification import RenderedMessage

logger = logging.getLogger(__name__)

PUSH_PATH = '/internal/push'
# Линтер принимает имя заголовка за пароль из-за слова token.
SERVICE_TOKEN_HEADER = 'X-Service-Token'  # noqa: S105


class WebsocketChannel(DeliveryChannel):
    """Доставка через websocket-шлюз."""

    def __init__(self, client: httpx.AsyncClient, service_token: str) -> None:
        self._client = client
        self._service_token = service_token

    @property
    def channel(self) -> Channel:
        return Channel.WEBSOCKET

    async def send(self, message: RenderedMessage) -> None:
        payload = {
            'user_id': str(message.user_id),
            'subject': message.subject,
            'body': message.body,
            'template_code': message.template_code,
        }
        headers = {
            SERVICE_TOKEN_HEADER: self._service_token,
            REQUEST_ID_HEADER: get_request_id(),
        }
        try:
            response = await self._client.post(PUSH_PATH, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ChannelUnavailableError(f'Websocket-шлюз недоступен: {error}') from error
        if not response.json().get('delivered'):
            # Зритель не в сети. Это не сбой: уведомление уже в истории и
            # ждёт его в личном кабинете.
            logger.info(
                'Зритель не в сети, мгновенное сообщение осталось в истории',
                extra={'user_id': str(message.user_id)},
            )
