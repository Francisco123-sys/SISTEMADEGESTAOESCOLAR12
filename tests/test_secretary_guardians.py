import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from werkzeug.security import check_password_hash

from app import app


class SecretaryGuardianManagementTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE students (id INTEGER PRIMARY KEY, code TEXT, nome TEXT);
            CREATE TABLE guardians (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
                contacto TEXT, ativo INTEGER DEFAULT 1, session_version INTEGER DEFAULT 0
            );
            CREATE TABLE guardian_students (
                guardian_id INTEGER NOT NULL, student_id INTEGER NOT NULL,
                parentesco TEXT, principal INTEGER DEFAULT 0,
                PRIMARY KEY (guardian_id, student_id)
            );
            INSERT INTO students VALUES (1, 'ALU-1', 'Aluno Um');
            INSERT INTO students VALUES (2, 'ALU-2', 'Aluno Dois');
        ''')
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = 5
            browser_session['_csrf_token'] = 'test-csrf-token'

    def tearDown(self):
        self.db.close()

    def test_create_guardian_and_associate_multiple_students(self):
        with (
            patch('routes.secretary.get_db', return_value=self.db),
            patch('routes.secretary.log_audit'),
        ):
            first = self.client.post('/secretary/encarregados', data={
                'csrf_token': 'test-csrf-token',
                'acao': 'associar',
                'student_id': '1',
                'email': 'parent@example.test',
                'nome': 'Encarregado Um',
                'contacto': '841234567',
                'password': 'new-guardian-password',
                'parentesco': 'Pai',
                'principal': '1',
            })
            second = self.client.post('/secretary/encarregados', data={
                'csrf_token': 'test-csrf-token',
                'acao': 'associar',
                'student_id': '2',
                'email': 'PARENT@example.test',
                'parentesco': 'Pai',
            })

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        password_hash = self.db.execute(
            'SELECT password_hash FROM guardians WHERE email = ?',
            ('parent@example.test',),
        ).fetchone()['password_hash']
        self.assertTrue(check_password_hash(password_hash, 'new-guardian-password'))
        self.assertEqual(
            self.db.execute('SELECT COUNT(*) FROM guardian_students').fetchone()[0],
            2,
        )

    def test_new_guardian_requires_strong_password(self):
        with patch('routes.secretary.get_db', return_value=self.db):
            response = self.client.post('/secretary/encarregados', data={
                'csrf_token': 'test-csrf-token',
                'acao': 'associar',
                'student_id': '1',
                'email': 'parent@example.test',
                'nome': 'Encarregado Um',
                'password': 'weak',
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM guardians').fetchone()[0], 0)

    def test_non_secretary_cannot_manage_guardian_accounts(self):
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'teacher'

        response = self.client.get('/secretary/encarregados')
        self.assertEqual(response.status_code, 302)

    def test_password_reset_revokes_existing_portal_sessions(self):
        self.db.execute('''
            INSERT INTO guardians (nome, email, password_hash, contacto)
            VALUES ('Encarregado', 'parent@example.test', 'old-hash', '841234567')
        ''')
        self.db.commit()
        guardian_id = self.db.execute(
            'SELECT id FROM guardians WHERE email = ?',
            ('parent@example.test',),
        ).fetchone()['id']
        with self.client.session_transaction() as browser_session:
            browser_session['guardian_id'] = guardian_id
            browser_session['guardian_session_version'] = 0

        with (
            patch('routes.secretary.get_db', return_value=self.db),
            patch('routes.secretary.log_audit'),
        ):
            response = self.client.post('/secretary/encarregados', data={
                'csrf_token': 'test-csrf-token',
                'acao': 'redefinir_password',
                'email': 'parent@example.test',
                'password': 'replacement-password',
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.db.execute(
                'SELECT session_version FROM guardians WHERE id = ?',
                (guardian_id,),
            ).fetchone()['session_version'],
            1,
        )


if __name__ == '__main__':
    unittest.main()
