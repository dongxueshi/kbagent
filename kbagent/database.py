"""Small parameterized MySQL session API; connections never share across threads."""

import re
from contextlib import contextmanager

import pymysql

from .config import Settings


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, rows, rowcount=0, lastrowid=None):
        self.rows = [Row(row) for row in rows]
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Session:
    def __init__(self, connection):
        self.raw = connection

    @staticmethod
    def prepare(query, params):
        if isinstance(params, dict):
            return re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", query)
        return query.replace("?", "%s")

    def execute(self, query, params=None):
        with self.raw.cursor() as cursor:
            cursor.execute(self.prepare(query, params), params)
            return Result(
                cursor.fetchall() if cursor.description else [], cursor.rowcount, cursor.lastrowid
            )

    def executemany(self, query, params):
        if not params:
            return Result([])
        with self.raw.cursor() as cursor:
            cursor.executemany(self.prepare(query, params[0]), params)
            return Result([], cursor.rowcount, cursor.lastrowid)


def connect(settings: Settings):
    if not settings.mysql_password.get_secret_value():
        raise RuntimeError("Set KBAGENT_MYSQL_PASSWORD in .env before starting")
    ssl = (
        {"ca": str(settings.mysql_ssl_ca), "check_hostname": True}
        if settings.mysql_ssl_ca
        else None
    )
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password.get_secret_value(),
        database=settings.mysql_database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
        ssl=ssl,
    )


@contextmanager
def transaction(settings):
    connection = connect(settings)
    try:
        yield Session(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
