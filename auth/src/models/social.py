"""Аккаунты в соцсетях, привязанные к учётной записи кинотеатра."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

# Имя поставщика: короткий идентификатор вроде yandex или google.
PROVIDER_PATTERN = r'^[a-z][a-z0-9_]*$'
PROVIDER_MAX_LENGTH = 32
SOCIAL_ID_MAX_LENGTH = 128

ProviderName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, max_length=PROVIDER_MAX_LENGTH, pattern=PROVIDER_PATTERN),
]


class SocialProfile(BaseModel):
    """Что поставщик рассказал о владельце аккаунта.

    Опознаём пользователя только по паре (поставщик, social_id): искать его по
    email или логину нельзя — email в соцсети могут сменить или не подтвердить,
    и тогда чужой аккаунт открыл бы доступ к чужой учётной записи.
    """

    model_config = ConfigDict(frozen=True)

    social_id: str
    # Показывается в личном кабинете, чтобы человек понял, какой это аккаунт.
    # Для опознания не используется и может отсутствовать.
    display_name: str | None = None
    email: str | None = None


class SocialAccount(BaseModel):
    """Связь учётной записи кинотеатра с аккаунтом в соцсети."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    user_id: UUID
    provider: str
    social_id: str
    display_name: str | None
    email: str | None
    created_at: datetime
