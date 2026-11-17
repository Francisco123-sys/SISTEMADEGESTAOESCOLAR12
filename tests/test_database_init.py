import contextlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from werkzeug.security import check_password_hash

from database import init_db as database_init
from database.init_db import _seed_initial_data


class DatabaseBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('''
            CREATE TABLE academic_years (
                id INTEGER PRIMARY KEY,
                ano INTEGER UNIQUE NOT NULL,
                ativo INTEGER DEFAULT 1
            );
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                numero INTEGER NOT NULL,
                nivel TEXT NOT NULL
            );
            CREATE TABLE subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                codigo TEXT UNIQUE NOT NULL
            );
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                contacto TEXT
            );
        ''')

    def tearDown(self):
        self.db.close()

    def test_bootstrap_does_not_create_default_accounts(self):
        with patch.dict('os.environ', {}, clear=True):
            has_admin = _seed_initial_data(self.db.cursor())

        self.assertFalse(has_admin)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)

    def test_bootstrap_creates_only_configured_admin(self):
        config = {
            'SIGE_INITIAL_ADMIN_EMAIL': 'admin@example.test',
            'SIGE_INITIAL_ADMIN_PASSWORD': 'a-long-test-password',
            'SIGE_INITIAL_ADMIN_NAME': 'Admin de Teste',
        }
        with patch.dict('os.environ', config, clear=True):
            has_admin = _seed_initial_data(self.db.cursor())

        self.assertTrue(has_admin)
        rows = self.db.execute(
            'SELECT nome, email, role, password_hash FROM users'
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0:3], ('Admin de Teste', 'admin@example.test', 'admin'))
        self.assertTrue(check_password_hash(rows[0][3], config['SIGE_INITIAL_ADMIN_PASSWORD']))

    def test_bootstrap_rejects_short_admin_password(self):
        config = {
            'SIGE_INITIAL_ADMIN_EMAIL': 'admin@example.test',
            'SIGE_INITIAL_ADMIN_PASSWORD': 'short',
        }
        with patch.dict('os.environ', config, clear=True):
            with self.assertRaises(ValueError):
                _seed_initial_data(self.db.cursor())

        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)

    def test_sqlite_initialization_migrates_incrementally_in_a_temporary_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'sige-test.db'
            with (
                patch.object(database_init, 'SQLITE_DATABASE_PATH', str(database_path)),
                patch.object(database_init, '_is_pg', return_value=False),
                patch.dict('os.environ', {}, clear=True),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                database_init.init_db()
                database_init.init_db()

            conn = sqlite3.connect(database_path)
            columns = {
                row[1] for row in conn.execute('PRAGMA table_info(turmas)').fetchall()
            }
            user_count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            guardian_tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            counters_exist = conn.execute('''
                SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'code_counters'
            ''').fetchone() is not None
            student_columns = {
                row[1] for row in conn.execute('PRAGMA table_info(students)').fetchall()
            }
            conn.close()

        self.assertIn('area_curricular', columns)
        self.assertIn('diretor_turma_id', columns)
        self.assertIn('guardians', guardian_tables)
        self.assertIn('guardian_students', guardian_tables)
        self.assertTrue(counters_exist)
        self.assertIn('encarregado_nome', student_columns)
        self.assertIn('documento_transferencia_path', student_columns)
        self.assertEqual(user_count, 0)


if __name__ == '__main__':
    unittest.main()
