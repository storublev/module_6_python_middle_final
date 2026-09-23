import abc
from json.decoder import JSONDecodeError
from typing import Any

from redis import Redis


class BaseStorage:
    @abc.abstractmethod
    def save_state(self, state: dict) -> None:
        """Сохранить состояние в постоянное хранилище"""
        pass

    @abc.abstractmethod
    def retrieve_state(self, key: str) -> dict:
        """Загрузить состояние из постоянного хранилища"""
        pass


class RedisStorage(BaseStorage):
    def __init__(self, redis_adapter: Redis):
        self.redis_adapter = redis_adapter

    def save_state(self, state: dict) -> None:
        for key, value in state.items():
            self.redis_adapter[key] = value

    def retrieve_state(self, key) -> dict | str | None:
        """Загрузить состояние из постоянного хранилища"""
        if self.redis_adapter.exists(key):
            return self.redis_adapter.get(key).decode('utf-8')
        return None

    def _clear_cache(self):
        """Очистить кэш"""
        self.redis_adapter.flushdb()


class State:
    """
    Класс для хранения состояния при работе с данными, чтобы постоянно не перечитывать данные с начала.
    """

    def __init__(self, storage: BaseStorage):
        self.storage = storage

    def set_state(self, key: str, value: Any) -> None:
        """Установить состояние для определённого ключа"""
        self.storage.save_state({key: value})

    def get_state(self, key: str, default=None) -> Any:
        """Получить состояние по определённому ключу"""
        try:
            state = self.storage.retrieve_state(key)
            if state:
                return state
            return default
        except (JSONDecodeError, KeyError):
            return default


if __name__ == '__main__':
    print("State is running")
