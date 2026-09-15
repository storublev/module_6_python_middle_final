from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Session(BaseModel):
    """Сессия — один вход пользователя на одном устройстве.

    Живёт, пока действует её refresh-токен. Хранит идентификатор (jti)
    единственного действующего refresh-токена: он одноразовый, при обновлении
    пары токенов jti меняется.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    user_id: UUID
    refresh_jti: str
