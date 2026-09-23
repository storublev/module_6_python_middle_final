import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from json.decoder import JSONDecodeError
from typing import List

from elasticsearch import ConnectionError
from pydantic import errors as pydantic_errors
from services.elastic_loader_service import ElasticsearchLoader
from services.postgres_extractor_service import PostgresExtractor
from services.state_service import State
from core.config import ETLConfig, sleep_time_seconds


@dataclass
class ETLHandler:
    """
    Класс, представляющий интерфейс для осуществления переноса данных из PostgreSQL в
    ElasticSearch в соответствии с конфигурацией параметров, указываемых в config.
    """
    extractor: PostgresExtractor
    loader: ElasticsearchLoader
    config: ETLConfig
    last_modified_date: datetime = None
    state_option: str = 'modified'

    def transform_data(self, rows: list):
        """Transform data for uploading to Elasticsearch."""
        try:
            return [
                {
                    "_index": self.config.elastic_index_name,
                    "_id": row['id'],
                    "_source": self.config.related_model(**row).json()
                } for row in rows
            ]
        except (pydantic_errors.Any, JSONDecodeError) as exc:
            logging.exception(exc)
            raise exc

    async def process(self, elastic_conn, state: State):
        self.last_modified_date = state.get_state(key=self.config.state_key, default='1970-01-01')
        formatted_query = self.config.query.format(
            last_md_date=self.last_modified_date,
            limit=self.config.limit_size)

        for batch in self.extractor.extract_batch_from_database(query=formatted_query,
                                                                fetch_size=self.config.batch_size):
            if batch:
                self.last_modified_date = state.get_state(key=self.config.state_key, default='1970-01-01')
                new_last_modified_date = batch[-1][self.state_option].isoformat()
                transformed_data = self.transform_data(rows=batch)

                try:
                    self.loader.load_data_to_elastic(elastic_conn, transformed_data)

                except ConnectionError as exc:
                    logging.exception(f'*** Error while loading to elastic: ***\n{exc}')
                    raise ConnectionError(exc) from exc

                else:
                    state.set_state(self.config.state_key, new_last_modified_date)
                    logging.info('\tExtracted %s rows for {self.config.elastic_index_name}', len(batch))
                    logging.info(
                        f'State "{self.config.state_key}" updated from {self.last_modified_date}'
                        f' to {new_last_modified_date}')

        logging.info('ETL for %s finished. Paused for %s seconds',
                     self.config.elastic_index_name, sleep_time_seconds)
        await asyncio.sleep(sleep_time_seconds)


def get_etl_handlers(conn, configs: List[ETLConfig]) -> List[ETLHandler]:
    """
    Функция возвращает список объектов, осуществляющих ETL.
    :param conn: соединение
    :param configs: список конфигураций ETL
    :return: список объектов осуществляющих ETL
    """
    return [
        ETLHandler(PostgresExtractor(conn.cursor()), ElasticsearchLoader(), config) for config in configs
    ]
