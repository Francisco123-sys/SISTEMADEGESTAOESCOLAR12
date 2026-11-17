import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from werkzeug.security import generate_password_hash
from werkzeug.security import check_password_hash

from app import app


class GuardianPortalTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE guardians (
                id INTEGER PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                contacto TEXT,
                ativo INTEGER NOT NULL,
                session_version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE guardian_students (
                guardian_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                parentesco TEXT,
                principal INTEGER DEFAULT 0,
                PRIMARY KEY (guardian_id, student_id)
            );
            CREATE TABLE auth_rate_limits (
                bucket_hash TEXT PRIMARY KEY,
                failures INTEGER NOT NULL,
                window_started_at REAL NOT NULL,
                blocked_until REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                nome TEXT,
                code TEXT,
                bi_cedula TEXT,
                foto_path TEXT,
                classe_ingresso INTEGER,
                curso TEXT,
                turno TEXT,
                turma_id INTEGER
            );
            CREATE TABLE turmas (id INTEGER PRIMARY KEY, nome TEXT, sala TEXT);
            CREATE TABLE grades (
                id INTEGER PRIMARY KEY, student_id INTEGER, subject_id INTEGER,
                turma_id INTEGER, trimestre INTEGER, av1 REAL, av2 REAL,
                av3 REAL, av4 REAL, media_trimestral REAL, exame REAL,
                media_final REAL, faltas_justificadas INTEGER,
                faltas_nao_justificadas INTEGER, total_faltas INTEGER,
                comentario_professor TEXT, status TEXT
            );
            CREATE TABLE subjects (id INTEGER PRIMARY KEY, nome TEXT);
            CREATE TABLE attendance (student_id INTEGER, tipo TEXT);
            CREATE TABLE reclamacoes_encarregados (
                id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER,
                nome_encarregado TEXT, contacto TEXT, mensagem TEXT,
                status TEXT DEFAULT 'Pendente', resposta_escola TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO students VALUES
                (10, 'Educando Associado', 'ALU-10', 'BI-10', NULL, 8, 'Diurno', 'Manhã', 1),
                (20, 'Educando Alheio', 'ALU-20', 'BI-20', NULL, 9, 'Diurno', 'Tarde', 1);
            INSERT INTO turmas VALUES (1, '8ª A', 'Sala 01');
            INSERT INTO subjects VALUES (1, 'Matemática');
            INSERT INTO grades VALUES (1, 10, 1, 1, 1, 12, 13, 14, 15, 13.5, NULL, 13.5, 0, 0, 0, '', 'Submetida');
            INSERT INTO grades VALUES (2, 20, 1, 1, 1, 19, 19, 19, 19, 19, NULL, 19, 0, 0, 0, '', 'Submetida');
        ''')
        self.db.executemany('''
            INSERT INTO guardians (id, nome, email, password_hash, contacto, ativo, session_version)
            VALUES (?, ?, ?, ?, ?, 1, 0)
        ''', (
            (1, 'Encarregada Teste', 'encarregada@example.test',
             generate_password_hash('guardian-password-123'), '841234567'),
            (2, 'Outra Conta', 'outra@example.test',
             generate_password_hash('another-password-123'), '849876543'),
        ))
        self.db.execute(
            'INSERT INTO guardian_students VALUES (1, 10, ?, 1)',
            ('Mãe',),
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_code_and_bi_do_not_grant_public_access(self):
        with patch('routes.auth.get_db', return_value=self.db):
            response = self.client.get('/portal-encarregado?codigo=BI-10')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Acesso do Encarregado', response.data)
        self.assertNotIn(b'Educando Associado', response.data)

    def test_guardian_login_shows_only_associated_students(self):
        with patch('routes.auth.get_db', return_value=self.db):
            self.client.get('/portal-encarregado')
            with self.client.session_transaction() as browser_session:
                token = browser_session['_csrf_token']
            response = self.client.post('/portal-encarregado', data={
                'acao': 'login',
                'csrf_token': token,
                'email': 'encarregada@example.test',
                'password': 'guardian-password-123',
            }, follow_redirects=True)

        self.assertIn(b'Educando Associado', response.data)
        self.assertNotIn(b'Educando Alheio', response.data)

    def test_guardian_cannot_read_another_students_grades(self):
        with patch('routes.auth.get_db', return_value=self.db):
            self.client.get('/portal-encarregado')
            with self.client.session_transaction() as browser_session:
                token = browser_session['_csrf_token']
            self.client.post('/portal-encarregado', data={
                'acao': 'login',
                'csrf_token': token,
                'email': 'encarregada@example.test',
                'password': 'guardian-password-123',
            })
            response = self.client.get('/portal-encarregado?aluno=20', follow_redirects=True)

        self.assertNotIn(b'Educando Alheio', response.data)
        self.assertNotIn(b'19.0', response.data)

    def test_complaint_rejects_unassociated_student(self):
        with patch('routes.auth.get_db', return_value=self.db):
            self.client.get('/portal-encarregado')
            with self.client.session_transaction() as browser_session:
                token = browser_session['_csrf_token']
            self.client.post('/portal-encarregado', data={
                'acao': 'login',
                'csrf_token': token,
                'email': 'encarregada@example.test',
                'password': 'guardian-password-123',
            })
            with self.client.session_transaction() as browser_session:
                token = browser_session['_csrf_token']
            response = self.client.post('/portal-encarregado', data={
                'acao': 'reclamacao',
                'csrf_token': token,
                'student_id': '20',
                'mensagem': 'Mensagem não autorizada',
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.db.execute('SELECT COUNT(*) FROM reclamacoes_encarregados').fetchone()[0],
            0,
        )

    def test_login_requires_csrf_token(self):
        with patch('routes.auth.get_db', return_value=self.db):
            response = self.client.post('/portal-encarregado', data={
                'acao': 'login',
                'email': 'encarregada@example.test',
                'password': 'guardian-password-123',
            })

        self.assertEqual(response.status_code, 400)
        with self.client.session_transaction() as browser_session:
            self.assertNotIn('guardian_id', browser_session)

    def test_guardian_can_change_password_and_keep_current_session(self):
        with patch('routes.auth.get_db', return_value=self.db):
            self.client.get('/portal-encarregado')
            with self.client.session_transaction() as browser_session:
                token = browser_session['_csrf_token']
            self.client.post('/portal-encarregado', data={
                'acao': 'login',
                'csrf_token': token,
                'email': 'encarregada@example.test',
                'password': 'guardian-password-123',
            })
            with self.client.session_transaction() as browser_session:
                token = browser_session['_csrf_token']
            response = self.client.post('/portal-encarregado', data={
                'acao': 'update_password',
                'csrf_token': token,
                'current_password': 'guardian-password-123',
                'new_password': 'changed-guardian-password',
            })
            portal = self.client.get('/portal-encarregado')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(portal.status_code, 200)
        changed_hash = self.db.execute(
            'SELECT password_hash FROM guardians WHERE id = 1'
        ).fetchone()['password_hash']
        self.assertTrue(check_password_hash(changed_hash, 'changed-guardian-password'))


if __name__ == '__main__':
    unittest.main()
