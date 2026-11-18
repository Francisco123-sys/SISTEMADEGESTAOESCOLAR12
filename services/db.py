import os
import re
import sqlite3
from urllib.parse import unquote, urlparse
from flask import g, has_request_context, request
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


def get_database_url(prefer_unpooled=False):
    if prefer_unpooled:
        names = (
            'DATABASE_URL_UNPOOLED',
            'POSTGRES_URL_NON_POOLING',
            'DATABASE_URL',
            'POSTGRES_URL',
            'POSTGRES_PRISMA_URL',
        )
    else:
        names = (
            'DATABASE_URL',
            'POSTGRES_URL',
            'POSTGRES_PRISMA_URL',
            'DATABASE_URL_UNPOOLED',
            'POSTGRES_URL_NON_POOLING',
        )
    return next(
        (os.getenv(name, '').strip() for name in names if os.getenv(name, '').strip()),
        '',
    )


def database_target_key(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname:
        raise ValueError('A URL da base de dados não é PostgreSQL válida.')
    hostname = re.sub(r'-pooler(?=\.|$)', '', parsed.hostname.lower())
    return (
        hostname,
        parsed.port or 5432,
        unquote(parsed.path.lstrip('/')),
        unquote(parsed.username or ''),
    )


def ensure_same_database_target(runtime_url, migration_url):
    if not runtime_url or not migration_url:
        raise RuntimeError('A ligação da aplicação e a ligação de migração devem estar configuradas.')
    if database_target_key(runtime_url) != database_target_key(migration_url):
        raise RuntimeError(
            'A ligação de migração aponta para uma base diferente da ligação seleccionada pela aplicação.'
        )


def is_postgres(url=None):
    url = url or get_database_url()
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
SCHEMA_TABLES = {'students', 'student_deletion_requests'}


def has_table(cursor, table_name):
    if table_name not in SCHEMA_TABLES:
        raise ValueError('Tabela fora da lista permitida.')
    if isinstance(cursor, sqlite3.Cursor):
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        )
        return cursor.fetchone() is not None

    cursor.execute('''
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema() AND table_name = ?
        ) AS present
    ''', (table_name,))
    return bool(cursor.fetchone()['present'])


def has_column(cursor, table_name, column_name):
    if table_name not in SCHEMA_TABLES or not re.fullmatch(r'[a-z_]+', column_name):
        raise ValueError('Tabela ou coluna fora da lista permitida.')
    if isinstance(cursor, sqlite3.Cursor):
        cursor.execute(f'PRAGMA table_info({table_name})')
        return any(row[1] == column_name for row in cursor.fetchall())

    cursor.execute('''
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ? AND column_name = ?
        ) AS present
    ''', (table_name, column_name))
    return bool(cursor.fetchone()['present'])


def active_student_condition(cursor, alias=''):
    if alias and not re.fullmatch(r'[a-z_]+', alias):
        raise ValueError('Alias de tabela inválido.')
    column = f'{alias}.ativo' if alias else 'ativo'
    return f'{column} = 1' if has_column(cursor, 'students', 'ativo') else '1 = 1'


def student_deletion_schema_ready(cursor):
    return (
        has_column(cursor, 'students', 'ativo')
        and has_table(cursor, 'student_deletion_requests')
    )


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


def create_standalone_connection(database_url=None):
    database_url = database_url or get_database_url()
    if is_postgres(database_url):
        if not HAS_PSYCOPG2:
            raise RuntimeError("psycopg2-binary não está instalado. Execute: pip install -r requirements.txt")
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        raw_conn = psycopg2.connect(database_url)
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


def log_audit(
    user_id,
    operacao,
    detalhes,
    ip=None,
    module=None,
    affected_record=None,
    change_details=None,
    result='Sucesso',
):
    if module is None:
        operation = operacao.upper()
        if any(term in operation for term in ('ALUNO', 'MATRICULA')):
            module = 'Alunos e matrículas'
        elif any(term in operation for term in ('TURMA', 'CLASSE')):
            module = 'Turmas'
        elif any(term in operation for term in ('NOTA', 'PAUTA')):
            module = 'Notas e pautas'
        elif 'PAGAMENTO' in operation:
            module = 'Pagamentos'
        elif 'RECLAMACAO' in operation:
            module = 'Reclamações'
        elif any(term in operation for term in ('FUNCIONARIO', 'UTILIZADOR', 'CONTA', 'SENHA')):
            module = 'Utilizadores e permissões'
        elif operation in ('LOGIN', 'LOGOUT'):
            module = 'Autenticação'
        else:
            module = 'Sistema'

    details = _redact_audit_credentials(str(detalhes))
    change = _redact_audit_credentials(
        str(change_details if change_details is not None else detalhes)
    )
    ip_address = ip or (
        request.remote_addr if has_request_context() else '127.0.0.1'
    )
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO audit_logs (
            user_id, operacao, detalhes, ip_address, module,
            affected_record, change_details, result
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_id, operacao, details, ip_address, module,
        affected_record, change, result,
    ))
    db.commit()


def _redact_audit_credentials(value):
    return re.sub(
        r'(?i)\b(password(?:_hash)?|senha|palavra[- ]?passe|token|secret)'
        r'\s*[:=]\s*([^\s,;]+)',
        r'\1=[REDACTED]',
        value,
    )
