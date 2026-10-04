from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Session(BaseModel):
    """Сессия — один вход пользователя на одном устройстве.

    Живёт, пока действует её refresh-токен. Хранит идентификатор (jti)
    единственного действующего refresh-токена: он одноразовый, при обновлении
    пары токенов jti меняется. Хранит и версию учётных данных пользователя на
    момент входа: после смены пароля сессия с прежней версией не действует.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    user_id: UUID
    refresh_jti: str
    credentials_version: int
