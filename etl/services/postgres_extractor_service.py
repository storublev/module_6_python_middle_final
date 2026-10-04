from db.connection_handler import PostgreConnError
from psycopg2 import InterfaceError, OperationalError, ProgrammingError
from psycopg2.extras import DictCursor


class PostgresExtractor:
    """ Класс осуществляющий запрос к базе данных PostgreSQL. """
    def __init__(self, db_cursor: DictCursor):
        self.db_cursor = db_cursor

    def extract_batch_from_database(self, query: str, fetch_size: int = 100) -> list:
        """
        Осуществляет запрос к БД и возвращает данные пачками размером fetch_size.
        :param query: SQL-запрос
        :param fetch_size: максимальный размер пачки возвращаемых данных
        :return: список строк, возвращенных из БД
        """
        try:
            self.db_cursor.execute(query)
            rows = self.db_cursor.fetchmany(fetch_size)
            while rows:
                yield rows
                rows = self.db_cursor.fetchmany(fetch_size)
        except (InterfaceError, OperationalError, ProgrammingError):
            self.db_cursor.close()
            raise PostgreConnError("Cursor problem") from None
