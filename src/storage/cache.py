from abc import ABC, abstractmethod


class Cache(ABC):
    """Интерфейс кеша: сервисы не зависят от конкретного хранилища."""

    @abstractmethod
    async def get(self, key: str) -> bytes | None:
        """Возвращает значение по ключу или None, если его нет."""

    @abstractmethod
    async def set(self, key: str, value: bytes | str, expire: int) -> None:
        """Сохраняет значение на expire секунд."""
