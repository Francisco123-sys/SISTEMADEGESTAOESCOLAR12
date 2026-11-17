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


def _initial_admin_config():
    email = os.getenv('SIGE_INITIAL_ADMIN_EMAIL', '').strip()
    password = os.getenv('SIGE_INITIAL_ADMIN_PASSWORD', '')
    if not email and not password:
        return None
    if not email or not password:
        raise ValueError(
            'Defina SIGE_INITIAL_ADMIN_EMAIL e SIGE_INITIAL_ADMIN_PASSWORD em conjunto.'
        )
    if len(password) < 12:
        raise ValueError('SIGE_INITIAL_ADMIN_PASSWORD deve ter pelo menos 12 caracteres.')
    return {
        'email': email,
        'password': password,
        'nome': os.getenv('SIGE_INITIAL_ADMIN_NAME', '').strip() or 'Administrador Geral',
        'contacto': os.getenv('SIGE_INITIAL_ADMIN_CONTACT', '').strip(),
    }


def _seed_initial_data(cursor, is_pg=False):
    admin_config = _initial_admin_config()
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
    if not cursor.fetchone() and admin_config:
        cursor.execute("SELECT code FROM users WHERE code LIKE 'ADM-%'")
        existing_codes = {row[0] for row in cursor.fetchall()}
        code_number = 1
        while f'ADM-{code_number:06d}' in existing_codes:
            code_number += 1
        code = f'ADM-{code_number:06d}'
        pwd_hash = generate_password_hash(admin_config['password'])
        if is_pg:
            cursor.execute('''
                INSERT INTO users (code, nome, email, password_hash, role, contacto)
                VALUES (%s, %s, %s, %s, 'admin', %s)
            ''', (code, admin_config['nome'], admin_config['email'], pwd_hash, admin_config['contacto']))
        else:
            cursor.execute('''
                INSERT INTO users (code, nome, email, password_hash, role, contacto)
                VALUES (?, ?, ?, ?, 'admin', ?)
            ''', (code, admin_config['nome'], admin_config['email'], pwd_hash, admin_config['contacto']))

    cursor.execute("SELECT 1 FROM users WHERE role = 'admin'")
    return cursor.fetchone() is not None


def init_db():
    _initial_admin_config()
    if _is_pg():
        if not HAS_PSYCOPG2:
            raise RuntimeError("psycopg2-binary não encontrado. Instale com: pip install -r requirements.txt")
        url = _get_pg_ddl_url()
        conn = psycopg2.connect(url)
        cursor = conn.cursor()

        with open('database/schema_pg.sql', 'r', encoding='utf-8') as f:
            cursor.execute(f.read())
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS code_counters (
                counter_key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            )
        ''')

        # Migrações seguras caso tabelas já existam no Neon
        for col, col_def in [
            ('faltas_justificadas', 'INTEGER DEFAULT 0'),
            ('faltas_nao_justificadas', 'INTEGER DEFAULT 0'),
            ('total_faltas', 'INTEGER DEFAULT 0'),
            ('comentario_professor', "TEXT DEFAULT ''")
        ]:
            cursor.execute(f"ALTER TABLE grades ADD COLUMN IF NOT EXISTS {col} {col_def}")

        cursor.execute("ALTER TABLE turmas ADD COLUMN IF NOT EXISTS sala TEXT DEFAULT 'Sala 01'")
        cursor.execute("""
            ALTER TABLE turmas ADD COLUMN IF NOT EXISTS area_curricular TEXT
            CHECK(area_curricular IN ('A', 'B', 'C') OR area_curricular IS NULL)
        """)
        cursor.execute("""
            ALTER TABLE turmas ADD COLUMN IF NOT EXISTS diretor_turma_id INTEGER
            REFERENCES users(id)
        """)
        for column, definition in (
            ('provincia_nascimento', 'TEXT'),
            ('nacionalidade', "TEXT DEFAULT 'Moçambicana'"),
            ('foto_path', 'TEXT'),
            ('pai_naturalidade', 'TEXT'),
            ('pai_profissao', 'TEXT'),
            ('pai_contacto', 'TEXT'),
            ('mae_naturalidade', 'TEXT'),
            ('mae_profissao', 'TEXT'),
            ('mae_contacto', 'TEXT'),
            ('encarregado_nome', 'TEXT'),
            ('encarregado_parentesco', 'TEXT'),
            ('encarregado_naturalidade', 'TEXT'),
            ('encarregado_profissao', 'TEXT'),
            ('encarregado_contacto_principal', 'TEXT'),
            ('encarregado_contacto_alternativo', 'TEXT'),
            ('encarregado_morada', 'TEXT'),
            ('encarregado_email', 'TEXT'),
            ('ingresso_situacao', "TEXT NOT NULL DEFAULT 'Interno' CHECK(ingresso_situacao IN ('Interno', 'Transferido'))"),
            ('escola_origem', 'TEXT'),
            ('provincia_origem', 'TEXT'),
            ('distrito_origem', 'TEXT'),
            ('classe_anterior', 'INTEGER'),
            ('ano_lectivo_anterior', 'INTEGER'),
            ('documento_transferencia_path', 'TEXT'),
            ('observacoes_transferencia', 'TEXT'),
        ):
            cursor.execute(
                f'ALTER TABLE students ADD COLUMN IF NOT EXISTS {column} {definition}'
            )
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS guardians (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                contacto TEXT,
                ativo INTEGER NOT NULL DEFAULT 1,
                session_version INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            ALTER TABLE guardians ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS guardian_password_resets (
                token_hash TEXT PRIMARY KEY,
                guardian_id INTEGER NOT NULL REFERENCES guardians(id) ON DELETE CASCADE,
                expires_at DOUBLE PRECISION NOT NULL,
                used_at DOUBLE PRECISION,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auth_rate_limits (
                bucket_hash TEXT PRIMARY KEY,
                failures INTEGER NOT NULL,
                window_started_at DOUBLE PRECISION NOT NULL,
                blocked_until DOUBLE PRECISION NOT NULL DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS guardian_students (
                guardian_id INTEGER NOT NULL REFERENCES guardians(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                parentesco TEXT,
                principal INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guardian_id, student_id)
            )
        ''')

        has_admin = _seed_initial_data(cursor, is_pg=True)
        conn.commit()
        cursor.close()
        conn.close()
        print("Base de dados PostgreSQL (Neon) SIGE inicializada e sincronizada com sucesso!")
        if not has_admin:
            print("Nenhum administrador foi criado. Configure SIGE_INITIAL_ADMIN_EMAIL e SIGE_INITIAL_ADMIN_PASSWORD e execute init-db novamente.")
    else:
        os.makedirs('database', exist_ok=True)
        conn = sqlite3.connect(SQLITE_DATABASE_PATH)
        cursor = conn.cursor()

        with open('database/schema.sql', 'r', encoding='utf-8') as f:
            cursor.executescript(f.read())
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS code_counters (
                counter_key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            )
        ''')

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
        if 'area_curricular' not in turma_cols:
            cursor.execute("""
                ALTER TABLE turmas ADD COLUMN area_curricular TEXT
                CHECK(area_curricular IN ('A', 'B', 'C') OR area_curricular IS NULL)
            """)
        if 'diretor_turma_id' not in turma_cols:
            cursor.execute("""
                ALTER TABLE turmas ADD COLUMN diretor_turma_id INTEGER
                REFERENCES users(id)
            """)
        cursor.execute("PRAGMA table_info(students)")
        student_cols = {row[1] for row in cursor.fetchall()}
        student_columns = (
            ('provincia_nascimento', 'TEXT'),
            ('nacionalidade', "TEXT DEFAULT 'Moçambicana'"),
            ('foto_path', 'TEXT'),
            ('pai_naturalidade', 'TEXT'),
            ('pai_profissao', 'TEXT'),
            ('pai_contacto', 'TEXT'),
            ('mae_naturalidade', 'TEXT'),
            ('mae_profissao', 'TEXT'),
            ('mae_contacto', 'TEXT'),
            ('encarregado_nome', 'TEXT'),
            ('encarregado_parentesco', 'TEXT'),
            ('encarregado_naturalidade', 'TEXT'),
            ('encarregado_profissao', 'TEXT'),
            ('encarregado_contacto_principal', 'TEXT'),
            ('encarregado_contacto_alternativo', 'TEXT'),
            ('encarregado_morada', 'TEXT'),
            ('encarregado_email', 'TEXT'),
            ('ingresso_situacao', "TEXT NOT NULL DEFAULT 'Interno' CHECK(ingresso_situacao IN ('Interno', 'Transferido'))"),
            ('escola_origem', 'TEXT'),
            ('provincia_origem', 'TEXT'),
            ('distrito_origem', 'TEXT'),
            ('classe_anterior', 'INTEGER'),
            ('ano_lectivo_anterior', 'INTEGER'),
            ('documento_transferencia_path', 'TEXT'),
            ('observacoes_transferencia', 'TEXT'),
        )
        for column, definition in student_columns:
            if column not in student_cols:
                cursor.execute(
                    f'ALTER TABLE students ADD COLUMN {column} {definition}'
                )
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS guardians (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                contacto TEXT,
                ativo INTEGER NOT NULL DEFAULT 1,
                session_version INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('PRAGMA table_info(guardians)')
        guardian_cols = {row[1] for row in cursor.fetchall()}
        if 'session_version' not in guardian_cols:
            cursor.execute('''
                ALTER TABLE guardians ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0
            ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS guardian_password_resets (
                token_hash TEXT PRIMARY KEY,
                guardian_id INTEGER NOT NULL REFERENCES guardians(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL,
                used_at REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auth_rate_limits (
                bucket_hash TEXT PRIMARY KEY,
                failures INTEGER NOT NULL,
                window_started_at REAL NOT NULL,
                blocked_until REAL NOT NULL DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS guardian_students (
                guardian_id INTEGER NOT NULL REFERENCES guardians(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                parentesco TEXT,
                principal INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guardian_id, student_id)
            )
        ''')

        has_admin = _seed_initial_data(cursor, is_pg=False)
        conn.commit()
        conn.close()
        print("Base de dados SQLite local SIGE inicializada com sucesso!")
        if not has_admin:
            print("Nenhum administrador foi criado. Configure SIGE_INITIAL_ADMIN_EMAIL e SIGE_INITIAL_ADMIN_PASSWORD e execute init-db novamente.")


if __name__ == '__main__':
    init_db()
