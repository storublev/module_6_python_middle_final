from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Response:
    """Ответ API, прочитанный целиком: сессия aiohttp к моменту проверок уже свободна."""

    status: int
    body: Any
