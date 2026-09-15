from abc import ABC, abstractmethod


class CacheUnavailableError(Exception):
    """Кеш не смог выполнить операцию: нет соединения, истёк таймаут, сбой хранилища."""


class Cache(ABC):
    """Интерфейс кеша: сервисы не зависят от конкретного хранилища.

    Контракт для всех реализаций: при любом сбое своего хранилища метод
    поднимает CacheUnavailableError и только его — ошибки конкретной
    библиотеки наружу не выходят. Сбой не маскируется под промах: решать,
    продолжать ли работу без кеша, должен вызывающий код.
    """

    @abstractmethod
    async def get(self, key: str) -> bytes | None:
        """Возвращает значение по ключу или None, если его нет.

        Raises:
            CacheUnavailableError: кеш не смог прочитать значение.
        """

    @abstractmethod
    async def set(self, key: str, value: bytes | str, expire: int) -> None:
        """Сохраняет значение на expire секунд.

        Raises:
            CacheUnavailableError: кеш не смог сохранить значение.
        """
