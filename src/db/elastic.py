from elasticsearch import AsyncElasticsearch

es: AsyncElasticsearch | None = None


async def get_elastic() -> AsyncElasticsearch:
    """Возвращает клиент Elasticsearch, созданный при старте приложения."""
    return es
