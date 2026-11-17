import sqlite3


COMPLAINT_COLUMNS = {
    'guardian_id': 'INTEGER REFERENCES guardians(id)',
    'categoria': 'TEXT',
    'assunto': 'TEXT',
    'estado': "TEXT NOT NULL DEFAULT 'Nova'",
    'updated_at': 'TIMESTAMP',
    'assigned_to': 'INTEGER REFERENCES users(id)',
    'forwarded_to_director': 'INTEGER NOT NULL DEFAULT 0',
    'forwarded_by': 'INTEGER REFERENCES users(id)',
    'forwarded_at': 'TIMESTAMP',
    'forward_reason': 'TEXT',
    'submission_key': 'TEXT',
}


def _existing_columns(cursor, is_postgres):
    if not is_postgres and isinstance(cursor, sqlite3.Cursor):
        cursor.execute('PRAGMA table_info(reclamacoes_encarregados)')
        return {row[1] for row in cursor.fetchall()}

    cursor.execute('''
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'reclamacoes_encarregados'
    ''')
    return {_column_name(row) for row in cursor.fetchall()}


def _column_name(row):
    try:
        return row['column_name']
    except (TypeError, KeyError):
        return row[0]


def _table_columns(cursor, table_name, is_postgres):
    if not is_postgres and isinstance(cursor, sqlite3.Cursor):
        cursor.execute(f'PRAGMA table_info({table_name})')
        return {row[1] for row in cursor.fetchall()}

    cursor.execute('''
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = ?
    ''', (table_name,))
    return {_column_name(row) for row in cursor.fetchall()}


def migrate_complaint_schema(cursor, is_postgres=False):
    columns = _existing_columns(cursor, is_postgres)
    if not columns:
        raise RuntimeError(
            'A tabela reclamacoes_encarregados não existe; aplique primeiro o esquema base.'
        )

    for column, definition in COMPLAINT_COLUMNS.items():
        if column in columns:
            continue
        cursor.execute(
            f'ALTER TABLE reclamacoes_encarregados ADD COLUMN {column} {definition}'
        )

    if 'status' in columns:
        cursor.execute('''
            UPDATE reclamacoes_encarregados
            SET estado = 'Respondida'
            WHERE status = 'Respondida' AND estado = 'Nova'
        ''')
    if 'created_at' in columns:
        cursor.execute('''
            UPDATE reclamacoes_encarregados
            SET updated_at = created_at
            WHERE updated_at IS NULL
        ''')

    if is_postgres:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS complaint_messages (
                id SERIAL PRIMARY KEY,
                complaint_id INTEGER NOT NULL
                    REFERENCES reclamacoes_encarregados(id) ON DELETE CASCADE,
                sender_type TEXT NOT NULL
                    CHECK(sender_type IN ('guardian', 'secretary', 'director')),
                sender_guardian_id INTEGER REFERENCES guardians(id),
                sender_user_id INTEGER REFERENCES users(id),
                message_key TEXT,
                body TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS complaint_status_history (
                id SERIAL PRIMARY KEY,
                complaint_id INTEGER NOT NULL
                    REFERENCES reclamacoes_encarregados(id) ON DELETE CASCADE,
                old_state TEXT,
                new_state TEXT NOT NULL,
                actor_type TEXT NOT NULL
                    CHECK(actor_type IN ('guardian', 'secretary', 'director')),
                actor_guardian_id INTEGER REFERENCES guardians(id),
                actor_user_id INTEGER REFERENCES users(id),
                note TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS complaint_notifications (
                id SERIAL PRIMARY KEY,
                event_key TEXT NOT NULL UNIQUE,
                complaint_id INTEGER NOT NULL
                    REFERENCES reclamacoes_encarregados(id) ON DELETE CASCADE,
                message_id INTEGER REFERENCES complaint_messages(id) ON DELETE CASCADE,
                recipient_guardian_id INTEGER REFERENCES guardians(id) ON DELETE CASCADE,
                recipient_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL
                    CHECK(kind IN ('new_complaint', 'new_message', 'status_changed', 'forwarded')),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP,
                CHECK ((recipient_guardian_id IS NULL) <> (recipient_user_id IS NULL))
            )
        ''')
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS complaint_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                complaint_id INTEGER NOT NULL
                    REFERENCES reclamacoes_encarregados(id) ON DELETE CASCADE,
                sender_type TEXT NOT NULL
                    CHECK(sender_type IN ('guardian', 'secretary', 'director')),
                sender_guardian_id INTEGER REFERENCES guardians(id),
                sender_user_id INTEGER REFERENCES users(id),
                message_key TEXT,
                body TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS complaint_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                complaint_id INTEGER NOT NULL
                    REFERENCES reclamacoes_encarregados(id) ON DELETE CASCADE,
                old_state TEXT,
                new_state TEXT NOT NULL,
                actor_type TEXT NOT NULL
                    CHECK(actor_type IN ('guardian', 'secretary', 'director')),
                actor_guardian_id INTEGER REFERENCES guardians(id),
                actor_user_id INTEGER REFERENCES users(id),
                note TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS complaint_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                complaint_id INTEGER NOT NULL
                    REFERENCES reclamacoes_encarregados(id) ON DELETE CASCADE,
                message_id INTEGER REFERENCES complaint_messages(id) ON DELETE CASCADE,
                recipient_guardian_id INTEGER REFERENCES guardians(id) ON DELETE CASCADE,
                recipient_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL
                    CHECK(kind IN ('new_complaint', 'new_message', 'status_changed', 'forwarded')),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP,
                CHECK ((recipient_guardian_id IS NULL) <> (recipient_user_id IS NULL))
            )
        ''')

    message_columns = _table_columns(cursor, 'complaint_messages', is_postgres)
    if 'message_key' not in message_columns:
        cursor.execute('ALTER TABLE complaint_messages ADD COLUMN message_key TEXT')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS uq_complaint_message_key
        ON complaint_messages(message_key)
    ''')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS uq_complaint_submission_key
        ON reclamacoes_encarregados(submission_key)
        WHERE submission_key IS NOT NULL
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS ix_complaint_inbox
        ON reclamacoes_encarregados(estado, updated_at, id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS ix_complaint_messages_thread
        ON complaint_messages(complaint_id, created_at, id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS ix_complaint_history_thread
        ON complaint_status_history(complaint_id, created_at, id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS ix_complaint_notifications_user
        ON complaint_notifications(recipient_user_id, read_at, created_at)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS ix_complaint_notifications_guardian
        ON complaint_notifications(recipient_guardian_id, read_at, created_at)
    ''')


def migrate_complaints():
    from services.db import create_standalone_connection, is_postgres

    db = create_standalone_connection()
    cursor = db.cursor()
    try:
        migrate_complaint_schema(cursor, is_postgres())
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()
