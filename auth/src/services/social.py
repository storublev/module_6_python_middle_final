"""Вход через соцсеть и управление связанными аккаунтами.

Вход состоит из двух запросов: `start` уводит пользователя к поставщику, а
`complete` принимает его обратно с одноразовым кодом. Между ними нужно
помнить, что вход начали именно мы, — этим занимается `state`: случайная
строка, которая уходит к поставщику и возвращается от него. Без неё чужой
ответ поставщика привязал бы к жертве чужой аккаунт (CSRF); поэтому state
одноразовый, живёт минуты и хранится в OAuthStateStore.

Пользователь опознаётся только по паре (поставщик, social_id). Искать его по
email нельзя: email в соцсети меняют и не всегда подтверждают, и тогда чужой
аккаунт открыл бы доступ к чужой учётной записи.

Тот же `complete` и заводит нового пользователя, и привязывает аккаунт к уже
вошедшему — разница лишь в том, был ли начат вход с токеном. Так поставщику
всё равно, чем закончится возврат, и второй обратный адрес не нужен.

Привязка длится дольше одного запроса, поэтому вместе со state запоминается и
вход, с которого её начали: перед самой привязкой сервис убеждается, что он
ещё действует. Иначе завладевший токеном закончил бы начатую привязку уже
после того, как владелец сменил пароль, и завёл бы себе способ входить снова.
"""

import json
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from models.social import SocialAccount, SocialProfile
from models.user import LOGIN_MAX_LENGTH, User
from services.auth import AuthService, ClientInfo, Principal
from services.errors import (
    LastLoginMethodError,
    OAuthStateInvalidError,
    ProviderNotFoundError,
    SocialAccountTakenError,
    SocialLinkExpiredError,
    TokenRevokedError,
)
from services.tokens import TokenPair
from storage.base import (
    AlreadyExistsError,
    OAuthProvider,
    OAuthStateStore,
    SocialAccountRepository,
    UnlinkResult,
    UserRepository,
)

logger = logging.getLogger(__name__)

# Длина случайного state: 32 байта не подберёшь за время его жизни.
STATE_BYTES = 32
# Логин пользователя, заведённого соцсетью: имя поставщика и его id.
# Уникален по построению — пара (поставщик, social_id) уникальна, — но
# на всякий случай при столкновении к нему добавляется случайный хвост.
LOGIN_SUFFIX_BYTES = 4


@dataclass(frozen=True)
class LinkTarget:
    """Кому привязать аккаунт и каким входом это начато.

    Привязка занимает два запроса, между которыми проходят минуты: за это
    время вход, с которого её начали, может закончиться — пользователь вышел
    или сменил пароль, потому что токен украли. Поэтому вместе с state
    запоминается сессия и версия учётных данных на момент начала, и перед
    самой привязкой они сверяются с текущими.
    """

    principal: Principal
    credentials_version: int


@dataclass(frozen=True)
class SocialLogin:
    """Итог возврата от поставщика."""

    tokens: TokenPair | None
    account: SocialAccount | None
    # Новая учётная запись заведена этим входом.
    created: bool


class SocialAuthService:
    """Вход через соцсеть, привязка и открепление аккаунтов."""

    def __init__(
        self,
        providers: dict[str, OAuthProvider],
        states: OAuthStateStore,
        accounts: SocialAccountRepository,
        users: UserRepository,
        auth: AuthService,
        state_ttl: timedelta,
    ):
        self.providers = providers
        self.states = states
        self.accounts = accounts
        self.users = users
        self.auth = auth
        self.state_ttl = state_ttl

    def get_provider(self, name: str) -> OAuthProvider:
        """Поставщик по имени.

        Raises:
            ProviderNotFoundError: такого поставщика нет или он не настроен.
        """
        provider = self.providers.get(name)
        if provider is None:
            raise ProviderNotFoundError(f'Unknown or not configured provider: {name}')
        return provider

    async def start(self, provider_name: str, redirect_uri: str, link_to: Principal | None = None) -> str:
        """Начинает вход: запоминает state и возвращает адрес поставщика.

        `link_to` — привязать аккаунт к уже вошедшему пользователю, а не
        входить: решение принимается здесь, а не на возврате, иначе чужой
        ответ поставщика мог бы привязаться к чужой учётной записи. Вместе с
        ним запоминается, каким входом начата привязка (см. LinkTarget).

        Raises:
            ProviderNotFoundError: такого поставщика нет.
        """
        provider = self.get_provider(provider_name)
        state = secrets.token_urlsafe(STATE_BYTES)
        link = None
        if link_to is not None:
            link = {
                'user_id': str(link_to.user_id),
                'session_id': str(link_to.session_id),
                'credentials_version': await self.users.get_credentials_version(link_to.user_id),
            }
        await self.states.save(
            state, json.dumps({'provider': provider.name, 'link_to': link}), ttl=self.state_ttl,
        )
        return await provider.authorization_url(state, redirect_uri)

    async def complete(
        self,
        provider_name: str,
        code: str,
        state: str,
        redirect_uri: str,
        client: ClientInfo,
    ) -> SocialLogin:
        """Принимает возврат от поставщика: меняет код на данные и входит или привязывает аккаунт.

        Raises:
            ProviderNotFoundError: такого поставщика нет.
            OAuthStateInvalidError: state не наш, просрочен или уже использован.
            SocialLinkExpiredError: вход, с которого начали привязку, больше не действует.
            ProviderRejectedError: поставщик не принял код.
            ProviderUnavailableError: поставщик не ответил.
            SocialAccountTakenError: аккаунт уже привязан к другой учётной записи.
        """
        provider = self.get_provider(provider_name)
        link_to = await self._take_state(state, provider.name)
        profile = await provider.fetch_profile(code, redirect_uri)
        if link_to is not None:
            await self._check_link_session(link_to)
            account = await self._link(link_to.principal.user_id, provider.name, profile)
            return SocialLogin(tokens=None, account=account, created=False)
        return await self._login(provider.name, profile, client)

    async def discard(self, state: str, provider_name: str) -> None:
        """Гасит начатый вход, который не состоялся: пользователь отказался у поставщика.

        Оставленный state сработал бы позже, а брошенный вход не должен
        ждать своего часа.
        """
        self.get_provider(provider_name)
        if state:
            await self.states.pop(state)

    async def list_accounts(self, user_id: UUID) -> list[SocialAccount]:
        """Привязанные аккаунты пользователя — для личного кабинета."""
        return await self.accounts.list_for_user(user_id)

    async def unlink(self, user_id: UUID, provider_name: str) -> bool:
        """Открепляет аккаунт соцсети; False — такого аккаунта у пользователя не было.

        Последний способ войти открепить нельзя: пользователь, заведённый
        соцсетью, пароля не знает и остался бы без доступа к аккаунту. Сначала
        пусть задаст пароль или привяжет другую соцсеть.

        Считает оставшиеся способы войти и открепляет аккаунт само хранилище,
        одной операцией: посчитай их здесь — и два одновременных запроса
        сняли бы две последние соцсети разом.

        Raises:
            LastLoginMethodError: это единственный способ войти.
        """
        result = await self.accounts.unlink(user_id, provider_name)
        if result is UnlinkResult.LAST_LOGIN_METHOD:
            raise LastLoginMethodError
        return result is UnlinkResult.UNLINKED

    async def _take_state(self, state: str, provider_name: str) -> LinkTarget | None:
        """Проверяет и гасит state; возвращает, кому привязать аккаунт, или None для входа."""
        payload = await self.states.pop(state) if state else None
        if payload is None:
            raise OAuthStateInvalidError
        data = json.loads(payload)
        # state выдан для другого поставщика — значит, ответ пришёл не туда.
        if data.get('provider') != provider_name:
            raise OAuthStateInvalidError
        link_to = data.get('link_to')
        if not link_to:
            return None
        return LinkTarget(
            principal=Principal(
                user_id=UUID(link_to['user_id']),
                session_id=UUID(link_to['session_id']),
            ),
            credentials_version=link_to['credentials_version'],
        )

    async def _check_link_session(self, link: LinkTarget) -> None:
        """Вход, с которого начали привязку, всё ещё действует.

        Иначе завладевший токеном закончил бы привязку и после того, как
        владелец сменил пароль или вышел, — и получил бы собственный способ
        входить в чужую учётную запись. Версия учётных данных проверяется
        отдельно от сессии: смену пароля с этого же устройства сессия
        переживает, а начатую привязку она всё равно отменяет.

        Raises:
            SocialLinkExpiredError: сессия закрыта или учётные данные сменились.
        """
        try:
            version = await self.auth.ensure_session(link.principal)
        except TokenRevokedError as exc:
            raise SocialLinkExpiredError from exc
        if version != link.credentials_version:
            raise SocialLinkExpiredError

    async def _link(self, user_id: UUID, provider_name: str, profile: SocialProfile) -> SocialAccount:
        try:
            return await self.accounts.link(user_id, provider_name, profile)
        except AlreadyExistsError as exc:
            raise SocialAccountTakenError from exc

    async def _login(self, provider_name: str, profile: SocialProfile, client: ClientInfo) -> SocialLogin:
        user = await self.accounts.get_user(provider_name, profile.social_id)
        created = user is None
        if user is None:
            user = await self._create_user(provider_name, profile)
        tokens = await self.auth.open_session(user, client)
        return SocialLogin(tokens=tokens, account=None, created=created)

    async def _create_user(self, provider_name: str, profile: SocialProfile) -> User:
        """Заводит учётную запись без пароля под аккаунт соцсети.

        Логин собирается из имени поставщика и его идентификатора: он не
        показывается никому, кроме владельца, и тот сменит его в личном
        кабинете. Брать логин или email из соцсети нельзя — они могут быть
        заняты в кинотеатре другим человеком.
        """
        login = self._make_login(provider_name, profile.social_id)
        try:
            return await self.accounts.create_user(login, provider_name, profile)
        except AlreadyExistsError:
            # Логин занят — почти невероятно, но тогда добавляем случайный хвост.
            suffix = secrets.token_hex(LOGIN_SUFFIX_BYTES)
            fallback = f'{login[:LOGIN_MAX_LENGTH - len(suffix) - 1]}-{suffix}'
            logger.warning('Логин %s занят, пользователь заведён как %s', login, fallback)
            try:
                return await self.accounts.create_user(fallback, provider_name, profile)
            except AlreadyExistsError as exc:
                # Второй раз столкнуться можно лишь если аккаунт уже привязан.
                raise SocialAccountTakenError from exc

    @staticmethod
    def _make_login(provider_name: str, social_id: str) -> str:
        allowed = ''.join(char for char in social_id if char.isalnum() or char in '_.@+-')
        return f'{provider_name}-{allowed or uuid4().hex}'[:LOGIN_MAX_LENGTH]
