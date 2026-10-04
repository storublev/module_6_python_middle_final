import logging
from contextlib import contextmanager

import backoff
import psycopg2
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError as ElasticConnectionError
from psycopg2.extras import DictCursor
from redis import Redis
from redis import exceptions as redis_exceptions

from .backoff_handlers import (elastic_conn_backoff_hdlr, pg_conn_backoff_hdlr,
                               pg_conn_success_hdlr, redis_conn_backoff_hdlr)


class PostgreConnError(Exception):
    pass


@backoff.on_exception(
    wait_gen=backoff.expo,
    exception=redis_exceptions.ConnectionError,
    on_backoff=redis_conn_backoff_hdlr,
    max_tries=10
)
def create_redis_connection(host):
    redis = Redis(host=host)
    redis.ping()
    return redis


@backoff.on_exception(
    wait_gen=backoff.expo,
    exception=ElasticConnectionError,
    on_backoff=elastic_conn_backoff_hdlr,
    max_tries=10
)
def create_elastic_connection(host, port):
    es = Elasticsearch(f'http://{host}:{port}')
    if not es.ping():
        raise ElasticConnectionError("Elastic server is not available")
    return es


@backoff.on_exception(
    wait_gen=backoff.expo,
    exception=psycopg2.Error,
    max_tries=10,
    on_backoff=pg_conn_backoff_hdlr,
    on_success=pg_conn_success_hdlr
)
def connect_db(params: dict):
    """
    Осуществляет соединение с БД PostgreSQL
    :param params: параметры соединения
    :return: экземпляр соединения с БД
    """
    return psycopg2.connect(
        **params,
        cursor_factory=DictCursor
    )


@contextmanager
def pg_context(params: dict):
    """Connection to db PostgreSQL."""
    conn = connect_db(params)
    yield conn
    conn.close()
    logging.info("Connection closed from context manager.")
