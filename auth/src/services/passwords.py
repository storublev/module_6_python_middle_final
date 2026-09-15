import asyncio

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher


class PasswordHasher:
    """Хеширование паролей функцией Argon2.

    Argon2 — KDF, намеренно медленная и требовательная к памяти: перебор
    утёкших хешей на видеокартах становится нерентабельным. Соль своя у
    каждого хеша и хранится в нём же вместе с параметрами:
    `$argon2id$v=19$m=65536,t=3,p=4$<соль>$<хеш>`.

    Хеширование занимает десятки миллисекунд процессорного времени, поэтому
    выполняется в пуле потоков, чтобы не останавливать цикл событий.
    """

    def __init__(self, hasher: Argon2Hasher | None = None):
        self._hash = PasswordHash((hasher or Argon2Hasher(),))
        # Хеш для сравнения, когда пользователя нет: вход по несуществующему
        # логину занимает столько же времени, сколько по существующему,
        # и по времени ответа нельзя узнать, зарегистрирован ли логин.
        self._dummy_hash = self._hash.hash('dummy password')

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hash.hash, password)

    async def verify(self, password: str, password_hash: str) -> bool:
        return await asyncio.to_thread(self._hash.verify, password, password_hash)

    async def verify_dummy(self, password: str) -> None:
        await self.verify(password, self._dummy_hash)
