"""Задания на публикацию в брокер, которые хранятся в базе (outbox).

Запись данных и публикация в RabbitMQ — два разных хранилища, и общей
транзакции у них нет. Поэтому публикация сначала становится строкой в той же
базе и в той же транзакции, что и данные, а в брокер её переносит отдельный
процесс. Либо записано и то и другое, либо ничего.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OutboxDraft(BaseModel):
    """Что положить в очередь, когда транзакция зафиксируется."""

    model_config = ConfigDict(frozen=True)

    stage: str
    payload: dict[str, Any]
    request_id: str
    # С какого момента публиковать. None — сразу. Будущее время — отложенное
    # задание: так ночное письмо ждёт утра в базе, а не ходит по кругу.
    available_at: datetime | None = None


class OutboxMessage(BaseModel):
    """Задание, которое ретранслятор забрал на публикацию."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    stage: str
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str
    attempts: int
