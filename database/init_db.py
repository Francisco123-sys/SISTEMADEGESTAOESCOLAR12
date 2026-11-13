import os
import sqlite3
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

SQLITE_DATABASE_PATH = 'database/sige.db'


def _get_pg_ddl_url():
    url = (
        os.getenv('DATABASE_URL_UNPOOLED')
        or os.getenv('POSTGRES_URL_NON_POOLING')
        or os.getenv('DATABASE_URL')
        or os.getenv('POSTGRES_URL')
        or ''
    ).strip()
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url


def _is_pg():
    url = _get_pg_ddl_url()
    return url.startswith('postgresql://')


def _seed_initial_data(cursor, is_pg=False):
    if is_pg:
        cursor.execute("INSERT INTO academic_years (id, ano, ativo) VALUES (1, 2026, 1) ON CONFLICT DO NOTHING")
        cursor.execute("SELECT setval(pg_get_serial_sequence('academic_years', 'id'), COALESCE((SELECT MAX(id) FROM academic_years), 1))")
    else:
        cursor.execute("INSERT OR IGNORE INTO academic_years (id, ano, ativo) VALUES (1, 2026, 1)")

    for c in range(1, 13):
        nivel = 'Primário' if c <= 6 else 'Secundário'
        if is_pg:
            cursor.execute(
                "INSERT INTO classes (id, numero, nivel) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (c, c, nivel)
            )
        else:
            cursor.execute("INSERT OR IGNORE INTO classes (id, numero, nivel) VALUES (?, ?, ?)", (c, c, nivel))

    if is_pg:
        cursor.execute("SELECT setval(pg_get_serial_sequence('classes', 'id'), COALESCE((SELECT MAX(id) FROM classes), 12))")

    disciplinas = [
        ('Português', 'POR'), ('Inglês', 'ING'), ('História', 'HIS'),
        ('Geografia', 'GEO'), ('Biologia', 'BIO'), ('Física', 'FIS'),
        ('Matemática', 'MAT'), ('Química', 'QUI'), ('Educação Visual', 'EDV'),
        ('Agro-pecuária', 'AGP'), ('Educação Física', 'EDF')
    ]
    for disc, cod in disciplinas:
        if is_pg:
            cursor.execute(
                "INSERT INTO subjects (nome, codigo) VALUES (%s, %s) ON CONFLICT (codigo) DO NOTHING",
                (disc, cod)
            )
        else:
            cursor.execute("INSERT OR IGNORE INTO subjects (nome, codigo) VALUES (?, ?)", (disc, cod))

    cursor.execute("SELECT id FROM users WHERE role = 'admin'")
    if not cursor.fetchone():
        pwd_hash = generate_password_hash("AdminSIGE2026!")
        if is_pg:
            cursor.execute('''
                INSERT INTO users (code, nome, email, password_hash, role, contacto)
                VALUES ('ADM-000001', 'Administrador Geral', 'admin@sige.co.mz', %s, 'admin', '840000000')
                ON CONFLICT (email) DO NOTHING
            ''', (pwd_hash,))
        else:
            cursor.execute('''
                INSERT INTO users (code, nome, email, password_hash, role, contacto)
                VALUES ('ADM-000001', 'Administrador Geral', 'admin@sige.co.mz', ?, 'admin', '840000000')
            ''', (pwd_hash,))

    profiles = [
        ('SEC-000001', 'Maria Catarina Carlos Penicela', 'secretaria@sige.co.mz', 'chefe_secretaria', 'Secretaria123!'),
        ('DIR-000001', 'Director Escolar', 'director@sige.co.mz', 'director', 'Director123!'),
        ('PED1-000001', 'Director Pedagógico 1º Ciclo', 'pedagogico1@sige.co.mz', 'pedagogico_1', 'Pedagogico123!'),
        ('PED2-000001', 'Director Pedagógico 2º Ciclo', 'pedagogico2@sige.co.mz', 'pedagogico_2', 'Pedagogico123!'),
        ('PROF-000001', 'Professor Silva', 'professor@sige.co.mz', 'teacher', 'Professor123!')
    ]
    for code, nome, email, role, pwd in profiles:
        if is_pg:
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            if not cursor.fetchone():
                cursor.execute('''
                    INSERT INTO users (code, nome, email, password_hash, role, contacto)
                    VALUES (%s, %s, %s, %s, %s, '841234567')
                    ON CONFLICT (email) DO NOTHING
                ''', (code, nome, email, generate_password_hash(pwd), role))
        else:
            cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
            if not cursor.fetchone():
                cursor.execute('''
                    INSERT INTO users (code, nome, email, password_hash, role, contacto)
                    VALUES (?, ?, ?, ?, ?, '841234567')
                ''', (code, nome, email, generate_password_hash(pwd), role))


def init_db():
    if _is_pg():
        if not HAS_PSYCOPG2:
            raise RuntimeError("psycopg2-binary não encontrado. Instale com: pip install -r requirements.txt")
        url = _get_pg_ddl_url()
        conn = psycopg2.connect(url)
        cursor = conn.cursor()

        with open('database/schema_pg.sql', 'r', encoding='utf-8') as f:
            cursor.execute(f.read())

        # Migrações seguras caso tabelas já existam no Neon
        for col, col_def in [
            ('faltas_justificadas', 'INTEGER DEFAULT 0'),
            ('faltas_nao_justificadas', 'INTEGER DEFAULT 0'),
            ('total_faltas', 'INTEGER DEFAULT 0'),
            ('comentario_professor', "TEXT DEFAULT ''")
        ]:
            cursor.execute(f"ALTER TABLE grades ADD COLUMN IF NOT EXISTS {col} {col_def}")

        cursor.execute("ALTER TABLE turmas ADD COLUMN IF NOT EXISTS sala TEXT DEFAULT 'Sala 01'")

        _seed_initial_data(cursor, is_pg=True)
        conn.commit()
        cursor.close()
        conn.close()
        print("Base de dados PostgreSQL (Neon) SIGE inicializada e sincronizada com sucesso!")
    else:
        os.makedirs('database', exist_ok=True)
        conn = sqlite3.connect(SQLITE_DATABASE_PATH)
        cursor = conn.cursor()

        with open('database/schema.sql', 'r', encoding='utf-8') as f:
            cursor.executescript(f.read())

        cursor.execute("PRAGMA table_info(grades)")
        existing_cols = [row[1] for row in cursor.fetchall()]
        for col, col_type in [
            ('faltas_justificadas', 'INTEGER DEFAULT 0'),
            ('faltas_nao_justificadas', 'INTEGER DEFAULT 0'),
            ('total_faltas', 'INTEGER DEFAULT 0'),
            ('comentario_professor', "TEXT DEFAULT ''")
        ]:
            if col not in existing_cols:
                cursor.execute(f"ALTER TABLE grades ADD COLUMN {col} {col_type}")

        cursor.execute("PRAGMA table_info(turmas)")
        turma_cols = [row[1] for row in cursor.fetchall()]
        if 'sala' not in turma_cols:
            cursor.execute("ALTER TABLE turmas ADD COLUMN sala TEXT DEFAULT 'Sala 01'")

        _seed_initial_data(cursor, is_pg=False)
        conn.commit()
        conn.close()
        print("Base de dados SQLite local SIGE inicializada com sucesso!")


if __name__ == '__main__':
    init_db()
