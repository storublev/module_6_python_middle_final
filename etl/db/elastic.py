"""Подключение к Elasticsearch и загрузка данных."""

import logging
import time
import functools
from typing import Iterable

from elasticsearch import Elasticsearch, helpers
from elasticsearch.exceptions import ConnectionError as ESConnectionError

from core.config import settings

logger = logging.getLogger(__name__)


def backoff_es(
    start_delay: float = 1.0,
    max_delay: float = 60.0,
    max_retries: int = 10,
):
    """Декоратор backoff для операций с Elasticsearch."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = start_delay
            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except ESConnectionError as exc:
                    last_exc = exc
                    logger.warning(
                        "[%d/%d] Elasticsearch недоступен: %s. Повтор через %.1f с",
                        attempt, max_retries, exc, delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, max_delay)
            logger.error("Исчерпаны все попытки подключения к Elasticsearch.")
            raise last_exc  # type: ignore
        return wrapper
    return decorator


def get_es_client() -> Elasticsearch:
    """Создаёт и возвращает клиент Elasticsearch с backoff."""

    @backoff_es(max_retries=10)
    def _connect():
        client = Elasticsearch(settings.elastic_host)
        client.info()  # Проверка доступности
        return client

    return _connect()


def ensure_index(client: Elasticsearch, index: str, body: dict) -> bool:
    """Создаёт индекс или добавляет в его маппинг новые поля.

    Маппинг строгий (dynamic: strict): документ с полем, которого нет в
    маппинге, Elasticsearch не примет. Поэтому, когда в схеме появляются
    новые поля, они добавляются в маппинг существующего индекса — добавлять
    поля можно без пересоздания индекса. Уже загруженные документы этих полей
    не содержат, их нужно переиндексировать.

    Args:
        client: Клиент Elasticsearch.
        index: Имя индекса.
        body: Настройки и маппинг индекса.

    Returns:
        True, если в маппинг добавлены поля и документы надо загрузить заново.
    """
    if not client.indices.exists(index=index):
        logger.info("Создаю индекс «%s»…", index)
        client.indices.create(index=index, body=body)
        logger.info("Индекс «%s» создан.", index)
        return False

    properties = body["mappings"]["properties"]
    current = client.indices.get_mapping(index=index)[index]["mappings"].get("properties", {})
    missing = {name: mapping for name, mapping in properties.items() if name not in current}
    if not missing:
        logger.debug("Индекс «%s» уже существует.", index)
        return False

    client.indices.put_mapping(index=index, properties=missing)
    logger.info("В маппинг «%s» добавлены поля: %s", index, ", ".join(missing))
    return True


def sync_documents(
    client: Elasticsearch,
    index: str,
    documents: dict[str, dict],
    ids: Iterable[str],
) -> int:
    """Приводит документы индекса в соответствие с данными PostgreSQL.

    Документы из ``documents`` индексируются (создаются или перезаписываются),
    а ID из ``ids``, для которых документа нет, удаляются из индекса:
    запись удалена из БД или больше не подходит под условия выборки.
    Удаление несуществующего документа ошибкой не считается.

    Args:
        client: Клиент Elasticsearch.
        index: Имя индекса.
        documents: Документы по ID.
        ids: Все ID, которые нужно синхронизировать.

    Returns:
        Количество успешно выполненных операций.

    Raises:
        BulkIndexError: если хотя бы одну операцию выполнить не удалось —
            изменения тогда не отмечаются обработанными и повторятся.
    """
    actions = [
        {"_op_type": "index", "_index": index, "_id": doc_id, "_source": doc}
        for doc_id, doc in documents.items()
    ]
    actions += [
        {"_op_type": "delete", "_index": index, "_id": doc_id}
        for doc_id in ids
        if doc_id not in documents
    ]
    if not actions:
        return 0

    success, _ = helpers.bulk(
        client.options(request_timeout=120),
        actions,
        ignore_status=(404,),
    )
    logger.info(
        "📤 «%s»: проиндексировано %d, удалено %d",
        index, len(documents), len(actions) - len(documents),
    )
    return success
