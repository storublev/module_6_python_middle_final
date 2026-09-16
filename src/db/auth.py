import httpx

client: httpx.AsyncClient | None = None


async def get_auth_client() -> httpx.AsyncClient:
    """Возвращает HTTP-клиент сервиса авторизации, созданный при старте приложения."""
    return client
