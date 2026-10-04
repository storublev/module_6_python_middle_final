import logging


def pg_conn_backoff_hdlr(details):
    logging.info("\t\n ==> Backing off {wait:0.1f} seconds after {tries} tries "
                 "connection to PostgreSQL".format(**details))


def pg_conn_success_hdlr(details):
    logging.info("==> Successfully connected to PostgreSQL")


def pg_getdata_backoff_hdlr(details):
    logging.info(
        "\t\n ==> Can't execute query PostgreSQL."
        "Backing off {wait:0.1f} seconds after {tries} tries"
        "Details: {args}".format(**details))


def pg_getdata_success_hdlr(details):
    logging.info("==> Query executed successfully to PostgreSQL.")


def elastic_load_data_backoff_hdlr(details):
    logging.info(
        "\t\n ==> Can't load data to Elastic query PostgreSQL. "
        "Backing off {wait:0.1f} seconds after {tries} tries "
        "Details: {args}".format(**details))


def elastic_conn_backoff_hdlr(details):
    logging.info(
        "\t\n ==> Elastic connection Error. "
        "Backing off {wait:0.1f} seconds after {tries} tries "
        "Details: {args}".format(**details))


def redis_conn_backoff_hdlr(details):
    logging.info(
        "\t\n ==> Redis connection Error. "
        "Backing off {wait:0.1f} seconds after {tries} tries "
        "Details: {args}".format(**details))
