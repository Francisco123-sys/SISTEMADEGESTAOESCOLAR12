import os
import re
import sqlite3
from flask import g
from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

SQLITE_DATABASE = 'database/sige.db'
DB_INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg2.IntegrityError) if HAS_PSYCOPG2 else (sqlite3.IntegrityError,)


def get_database_url():
    return (
        os.getenv('DATABASE_URL')
        or os.getenv('POSTGRES_URL')
        or os.getenv('DATABASE_URL_UNPOOLED')
        or ''
    ).strip()


def is_postgres():
    url = get_database_url()
    return url.startswith('postgres://') or url.startswith('postgresql://')


def _adapt_sql_for_postgres(sql):
    # Convert SQLite '?' placeholders to PostgreSQL '%s'
    adapted = sql.replace('?', '%s')
    # Convert SQLite double-quoted string literals used in static filters
    adapted = adapted.replace('= "teacher"', "= 'teacher'")
    adapted = adapted.replace('= "secretary"', "= 'secretary'")
    adapted = adapted.replace('= "Falta"', "= 'Falta'")
    adapted = adapted.replace('!= "Nenhuma"', "!= 'Nenhuma'")
    adapted = adapted.replace('!= "admin"', "!= 'admin'")
    return adapted


TABLES_WITHOUT_ID = {
    'auth_rate_limits',
    'code_counters',
    'guardian_password_resets',
    'guardian_students',
}


class PgCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor
        self._lastrowid = None

    @property
    def lastrowid(self):
        return self._lastrowid

    def execute(self, sql, params=None):
        self._lastrowid = None
        sql_clean = _adapt_sql_for_postgres(sql)
        insert_match = re.match(
            r'^\s*INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)\b',
            sql_clean,
            re.IGNORECASE,
        )
        has_returning = bool(re.search(r'\bRETURNING\b', sql_clean, re.IGNORECASE))

        if (
            insert_match
            and not has_returning
            and insert_match.group(1).lower() not in TABLES_WITHOUT_ID
        ):
            sql_exec = sql_clean.rstrip().rstrip(';') + ' RETURNING id'
            if params is not None:
                self._cursor.execute(sql_exec, params)
            else:
                self._cursor.execute(sql_exec)
            row = self._cursor.fetchone()
            if row is not None:
                self._lastrowid = row['id'] if isinstance(row, dict) else row[0]
            return self

        if params is not None:
            self._cursor.execute(sql_clean, params)
        else:
            self._cursor.execute(sql_clean)
        return self

    def executemany(self, sql, seq_of_params):
        sql_clean = _adapt_sql_for_postgres(sql)
        self._cursor.executemany(sql_clean, seq_of_params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        self._cursor.close()

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class PgConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return PgCursorWrapper(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def create_standalone_connection():
    if is_postgres():
        if not HAS_PSYCOPG2:
            raise RuntimeError("psycopg2-binary não está instalado. Execute: pip install -r requirements.txt")
        url = get_database_url()
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        raw_conn = psycopg2.connect(url)
        return PgConnectionWrapper(raw_conn)
    else:
        os.makedirs('database', exist_ok=True)
        conn = sqlite3.connect(SQLITE_DATABASE)
        conn.row_factory = sqlite3.Row
        return conn


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = create_standalone_connection()
    return db


def close_db(exception=None):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def log_audit(user_id, operacao, detalhes, ip="127.0.0.1"):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO audit_logs (user_id, operacao, detalhes, ip_address)
        VALUES (?, ?, ?, ?)
    ''', (user_id, operacao, detalhes, ip))
    db.commit()
