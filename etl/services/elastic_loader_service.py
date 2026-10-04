import logging

from db.connection_handler import create_elastic_connection
from elasticsearch import (BadRequestError, ConnectionError, Elasticsearch,
                           TransportError, helpers)


class ElasticsearchLoader:
    """ Класс, предоставляющий методы загрузки в ElasticSearch данных. """
    @staticmethod
    def create_index(index_name: str, index_schema: dict, elastic_conn: Elasticsearch) -> None:
        """
        Метод, создающий индекс в ElasticSearch
        :param index_name: название индекса в ElasticSearch
        :param index_schema: схема индекса в формате dict(json)
        :param elastic_conn: экземпляр соединения с ElasticSearch
        :return: None
        """
        try:
            elastic_conn.indices.create(index=index_name, **index_schema)
        except BadRequestError:
            logging.info('Index %s insertion error -> {exc}', index_name)

    @staticmethod
    def load_data_to_elastic(elastic_conn: Elasticsearch, transformed_data: list) -> None:
        """
        Метод непосредственно загружает список данных в ElasticSearch
        :param elastic_conn: экземпляр соединения с ElasticSearch
        :param transformed_data: список данных, пригодных для загрузки в ElasticSearch
        :return: None
        """
        helpers.bulk(elastic_conn, transformed_data)


def create_indices(etl_handlers: list, elastic: Elasticsearch) -> Elasticsearch:
    """
    Функция создает индексы в ElasticSearch.
    При ошибке соединения происходит попытка восстановить подключение.
    Если индекс уже существует, ошибка перехватывается и логируется.
    :param etl_handlers: список объектов осуществляющих ETL
    :param elastic: соединение ElasticSearch
    :return: соединение ElasticSearch
    """
    while True:
        try:
            for etl_handler in etl_handlers:
                etl_handler.loader.create_index(
                    index_name=etl_handler.config.elastic_index_name,
                    index_schema=etl_handler.config.index_schema,
                    elastic_conn=elastic)
        except (ConnectionError, TransportError) as elastic_exc:
            logging.exception('Elastic connection error. Creation indices aborted\n\t %s', elastic_exc)
            elastic = create_elastic_connection()
        else:
            return elastic
