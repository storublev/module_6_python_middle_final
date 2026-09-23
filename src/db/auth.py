import httpx

client: httpx.AsyncClient | None = None


async def get_auth_client() -> httpx.AsyncClient:
    """Возвращает HTTP-клиент сервиса авторизации, созданный при старте приложения.

    Raises:
        RuntimeError: клиент не создан — значит, приложение не прошло lifespan.
    """
    if client is None:
        raise RuntimeError('HTTP-клиент сервиса авторизации не создан')
    return client
