from elasticsearch import AsyncElasticsearch

es: AsyncElasticsearch | None = None


async def get_elastic() -> AsyncElasticsearch:
    """Возвращает клиент Elasticsearch, созданный при старте приложения.

    Raises:
        RuntimeError: клиент не создан — значит, приложение не прошло lifespan.
    """
    if es is None:
        raise RuntimeError('Клиент Elasticsearch не создан')
    return es
