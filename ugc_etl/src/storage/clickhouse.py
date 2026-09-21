"""Приёмник событий: ClickHouse.

Единственное место, где ETL знает про ClickHouse.

Вставка идёт пачками по несколько тысяч строк. Это не оптимизация, а условие
работы: каждая вставка в MergeTree создаёт на диске новый кусок, который потом
сливается с остальными. Вставлять события по одному — значит завалить
хранилище кусками и получить ошибку «too many parts» вместо аналитики.
"""

import logging
from collections.abc import Sequence
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.exceptions import (
    ClickHouseError,
    DataError,
    IntegrityError,
    NotSupportedError,
    ProgrammingError,
)

from models.event import COLUMN_TYPES, COLUMNS
from storage.base import EventSink, SinkDataError, SinkUnavailableError

logger = logging.getLogger(__name__)

# Ошибки, означающие, что дело в самих данных, а не в хранилище: повторять
# такую вставку бессмысленно. Проверено на живом ClickHouse: значение вне
# диапазона UInt32 драйвер отдаёт как DataError ещё до отправки на сервер.
DATA_ERRORS = (DataError, IntegrityError, NotSupportedError, ProgrammingError)


class ClickHouseEventSink(EventSink):
    """Вставка строк событий в таблицу ClickHouse."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        table: str,
        *,
        connect_timeout: float = 5.0,
        send_receive_timeout: float = 60.0,
        client_factory=clickhouse_connect.get_client,
    ) -> None:
        self._table = table
        self._database = database
        self._client = client_factory(
            host=host,
            port=port,
            username=user,
            password=password,
            database=database,
            connect_timeout=connect_timeout,
            send_receive_timeout=send_receive_timeout,
        )

    def insert(self, rows: Sequence[Sequence[Any]]) -> None:
        """Вставляет пачку строк одним запросом."""
        if not rows:
            return
        try:
            # Типы колонок передаются явно: без них драйвер выводит их по
            # первой строке, и пачка, где первый фильм не указан, уехала бы
            # с неверным типом колонки.
            self._client.insert(
                self._table,
                rows,
                column_names=COLUMNS,
                column_type_names=COLUMN_TYPES,
                database=self._database,
            )
        except DATA_ERRORS as error:
            logger.error('ClickHouse не принял данные %d строк: %s', len(rows), error)
            raise SinkDataError(str(error)) from error
        except ClickHouseError as error:
            logger.warning('ClickHouse не ответил на вставку %d строк: %s', len(rows), error)
            raise SinkUnavailableError(str(error)) from error
        except OSError as error:
            # Обрыв соединения драйвер отдаёт исключением сокета, а не своим.
            logger.warning('Соединение с ClickHouse потеряно: %s', error)
            raise SinkUnavailableError(str(error)) from error

    def close(self) -> None:
        try:
            self._client.close()
        except (ClickHouseError, OSError) as error:
            logger.warning('Клиент ClickHouse закрылся с ошибкой: %s', error)
