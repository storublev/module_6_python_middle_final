import logging

from storage.base import AccessCache, AccessInvalidation, AccessInvalidationQueue, StorageUnavailableError

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


class AccessInvalidator:
    """Сбрасывает кеш прав по заданиям, записанным вместе с изменением ролей.

    Изменение роли фиксируется в PostgreSQL, а кеш лежит в Redis: сделать это
    одной транзакцией нельзя. Поэтому вместе с изменением в той же транзакции
    записывается задание на сброс кеша. Задание выполняется сразу после
    изменения, а если Redis недоступен — остаётся в базе, и его повторяет
    фоновая задача сервиса, пока кеш не будет сброшен.
    """

    def __init__(self, queue: AccessInvalidationQueue, cache: AccessCache):
        self.queue = queue
        self.cache = cache

    async def flush(self) -> int:
        """Выполняет все задания; возвращает, сколько выполнено.

        Raises:
            StorageUnavailableError: PostgreSQL или Redis недоступны — невыполненные задания остаются.
        """
        done = 0
        while processed := await self.queue.process(self._apply, limit=BATCH_SIZE):
            done += processed
            if processed < BATCH_SIZE:
                break
        return done

    async def flush_or_defer(self) -> None:
        """Выполняет задания, а при сбое хранилища оставляет их фоновому повтору.

        Изменение ролей к этому моменту уже зафиксировано вместе с заданием,
        поэтому сбой сброса кеша не делает изменение неудачным.
        """
        try:
            await self.flush()
        except StorageUnavailableError as exc:
            logger.warning('Кеш прав не сброшен, задание выполнит фоновый повтор: %s', exc)

    async def _apply(self, tasks: list[AccessInvalidation]) -> None:
        # Сброс всех прав включает сброс прав каждого пользователя.
        if any(task.user_id is None for task in tasks):
            await self.cache.invalidate_all()
            return
        for user_id in dict.fromkeys(task.user_id for task in tasks):
            await self.cache.invalidate_user(user_id)
