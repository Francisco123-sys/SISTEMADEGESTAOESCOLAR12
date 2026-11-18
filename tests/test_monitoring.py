import sqlite3
import unittest
from flask import Flask, g

from services.db import log_audit
from services.monitoring import (
    current_counts,
    recent_session_details,
    touch_active_session,
)


class ActiveSessionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('''
            CREATE TABLE active_sessions (
                account_key TEXT PRIMARY KEY, role TEXT NOT NULL,
                display_name TEXT NOT NULL, section TEXT NOT NULL,
                login_at REAL NOT NULL, last_activity REAL NOT NULL
            )
        ''')
        self.cursor = self.db.cursor()

    def tearDown(self):
        self.db.close()

    def test_multiple_tabs_share_one_active_person_and_keep_login_time(self):
        touch_active_session(self.cursor, 'staff:12', 'teacher', 'Professor Teste', 1000)
        touch_active_session(self.cursor, 'staff:12', 'teacher', 'Professor Teste', 1060)

        self.assertEqual(self.db.execute(
            'SELECT COUNT(*) FROM active_sessions'
        ).fetchone()[0], 1)
        self.assertEqual(tuple(self.db.execute(
            'SELECT login_at, last_activity FROM active_sessions'
        ).fetchone()), (1000.0, 1060.0))
        counts = current_counts(self.cursor, 1100)
        self.assertEqual(
            next(item['count'] for item in counts if item['section'] == 'Professor'),
            1,
        )

    def test_idle_sessions_are_inactive_then_expire(self):
        touch_active_session(
            self.cursor, 'guardian:2', 'guardian', 'Encarregado Teste', 1000
        )

        self.assertEqual(
            recent_session_details(self.cursor, 1000 + 6 * 60)[0]['status'],
            'Inactiva',
        )
        current_counts(self.cursor, 1000 + 31 * 60)
        self.assertEqual(self.db.execute(
            'SELECT COUNT(*) FROM active_sessions'
        ).fetchone()[0], 0)


class AuditLogTests(unittest.TestCase):
    def test_audit_record_contains_structured_fields_and_redacts_credentials(self):
        app = Flask(__name__)
        app.secret_key = 'test-secret'
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.execute('''
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY, user_id INTEGER, operacao TEXT NOT NULL,
                detalhes TEXT NOT NULL, ip_address TEXT, module TEXT,
                affected_record TEXT, change_details TEXT,
                result TEXT NOT NULL DEFAULT 'Sucesso',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        with app.test_request_context('/', environ_overrides={'REMOTE_ADDR': '192.0.2.4'}):
            g._database = db
            log_audit(
                7,
                'EDITAR_ALUNO',
                'Aluno A; password=do-not-log',
                module='Alunos e matrículas',
                affected_record='Aluno A',
                change_details='Dados actualizados; token=private-value',
            )
        row = db.execute('SELECT * FROM audit_logs').fetchone()

        self.assertEqual(row['module'], 'Alunos e matrículas')
        self.assertEqual(row['affected_record'], 'Aluno A')
        self.assertEqual(row['ip_address'], '192.0.2.4')
        self.assertNotIn('do-not-log', row['detalhes'])
        self.assertNotIn('private-value', row['change_details'])
        self.assertEqual(row['result'], 'Sucesso')
        db.close()


if __name__ == '__main__':
    unittest.main()
