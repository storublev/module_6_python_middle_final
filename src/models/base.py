from uuid import UUID

from pydantic import BaseModel


class IdModel(BaseModel):
    """Базовая модель документа Elasticsearch с идентификатором."""

    id: UUID
