import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app
from database.complaint_migrations import migrate_complaint_schema


class ComplaintMigrationTests(unittest.TestCase):
    def test_existing_schema_migrates_repeatedly_without_losing_legacy_data(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, ativo INTEGER);
            CREATE TABLE guardians (id INTEGER PRIMARY KEY, ativo INTEGER);
            CREATE TABLE reclamacoes_encarregados (
                id INTEGER PRIMARY KEY, student_id INTEGER, nome_encarregado TEXT,
                contacto TEXT, mensagem TEXT, status TEXT, resposta_escola TEXT,
                created_at TIMESTAMP
            );
            CREATE TABLE complaint_messages (
                id INTEGER PRIMARY KEY, complaint_id INTEGER, sender_type TEXT,
                sender_guardian_id INTEGER, sender_user_id INTEGER,
                body TEXT, created_at TIMESTAMP
            );
            INSERT INTO reclamacoes_encarregados VALUES
                (1, 10, 'Encarregada', '', 'Pedido antigo', 'Respondida',
                 'Resposta antiga', '2026-01-03 09:00:00');
        ''')
        try:
            migrate_complaint_schema(db.cursor())
            migrate_complaint_schema(db.cursor())
            complaint = db.execute('''
                SELECT mensagem, resposta_escola, estado, updated_at
                FROM reclamacoes_encarregados WHERE id = 1
            ''').fetchone()
            message_columns = {
                row['name'] for row in db.execute('PRAGMA table_info(complaint_messages)')
            }
            self.assertEqual(complaint['mensagem'], 'Pedido antigo')
            self.assertEqual(complaint['resposta_escola'], 'Resposta antiga')
            self.assertEqual(complaint['estado'], 'Respondida')
            self.assertEqual(str(complaint['updated_at'])[:10], '2026-01-03')
            self.assertIn('message_key', message_columns)
        finally:
            db.close()


class ComplaintWorkflowTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, nome TEXT, role TEXT,
                ativo INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE guardians (
                id INTEGER PRIMARY KEY, nome TEXT NOT NULL, email TEXT NOT NULL,
                password_hash TEXT NOT NULL, contacto TEXT, ativo INTEGER NOT NULL,
                session_version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE guardian_students (
                guardian_id INTEGER NOT NULL, student_id INTEGER NOT NULL,
                PRIMARY KEY (guardian_id, student_id)
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY, nome TEXT, code TEXT, bi_cedula TEXT,
                foto_path TEXT, classe_ingresso INTEGER, curso TEXT, turno TEXT,
                turma_id INTEGER, ativo INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE turmas (id INTEGER PRIMARY KEY, nome TEXT, sala TEXT);
            CREATE TABLE subjects (id INTEGER PRIMARY KEY, nome TEXT);
            CREATE TABLE grades (
                id INTEGER PRIMARY KEY, student_id INTEGER, subject_id INTEGER,
                turma_id INTEGER, trimestre INTEGER, av1 REAL, av2 REAL,
                av3 REAL, av4 REAL, media_trimestral REAL, exame REAL,
                media_final REAL, faltas_justificadas INTEGER,
                faltas_nao_justificadas INTEGER, total_faltas INTEGER,
                comentario_professor TEXT, status TEXT
            );
            CREATE TABLE attendance (student_id INTEGER, tipo TEXT);
            CREATE TABLE reclamacoes_encarregados (
                id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL,
                nome_encarregado TEXT NOT NULL, contacto TEXT NOT NULL,
                mensagem TEXT NOT NULL,
                status TEXT DEFAULT 'Pendente'
                    CHECK(status IN ('Pendente', 'Respondida')),
                resposta_escola TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO guardians VALUES
                (1, 'Encarregada Teste', 'guardian@example.test', 'hash', '841234567', 1, 0);
            INSERT INTO users VALUES
                (1, 'Secretaria Teste', 'secretary', 1),
                (2, 'Director Teste', 'director', 1);
            INSERT INTO students VALUES
                (10, 'Educando Associado', 'ALU-10', NULL, NULL, 8, 'Diurno', 'Manhã', 1, 1),
                (20, 'Educando Alheio', 'ALU-20', NULL, NULL, 8, 'Diurno', 'Manhã', 1, 1);
            INSERT INTO turmas VALUES (1, '8.ª A', 'Sala 01');
            INSERT INTO guardian_students VALUES (1, 10);
        ''')
        migrate_complaint_schema(self.db.cursor())
        self.db.commit()
        with self.client.session_transaction() as browser_session:
            browser_session['_csrf_token'] = 'test-csrf-token'

    def tearDown(self):
        self.db.close()

    def _set_guardian_session(self):
        with self.client.session_transaction() as browser_session:
            browser_session['guardian_id'] = 1
            browser_session['guardian_nome'] = 'Encarregada Teste'
            browser_session['guardian_session_version'] = 0

    def _set_staff_session(self, role, user_id):
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = role
            browser_session['user_id'] = user_id
            browser_session['user_nome'] = 'Utilizador de teste'

    def test_guardian_submission_is_idempotent_and_notifies_secretary(self):
        self._set_guardian_session()
        payload = {
            'csrf_token': 'test-csrf-token',
            'acao': 'reclamacao',
            'student_id': '10',
            'categoria': 'Matrícula',
            'assunto': 'Dúvida sobre matrícula',
            'mensagem': 'Solicito esclarecimento sobre a matrícula.',
            'submission_key': 'valid-test-submission-key-001',
        }
        with patch('routes.auth.get_db', return_value=self.db):
            first = self.client.post('/portal-encarregado', data=payload)
            second = self.client.post('/portal-encarregado', data=payload)
            portal = self.client.get('/portal-encarregado?aluno=10')

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(portal.status_code, 200)
        self.assertIn(b'D\xc3\xbavida sobre matr\xc3\xadcula', portal.data)
        self.assertIn(b'Solicito esclarecimento', portal.data)
        self.assertEqual(
            self.db.execute('SELECT COUNT(*) FROM reclamacoes_encarregados').fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.execute('SELECT COUNT(*) FROM complaint_messages').fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM complaint_notifications WHERE kind = 'new_complaint'"
            ).fetchone()[0],
            1,
        )

    def test_guardian_cannot_reply_to_unassociated_student_complaint(self):
        self.db.execute('''
            INSERT INTO reclamacoes_encarregados (
                student_id, nome_encarregado, contacto, mensagem
            ) VALUES (20, 'Outro', '', 'Não autorizada')
        ''')
        complaint_id = self.db.execute(
            'SELECT id FROM reclamacoes_encarregados'
        ).fetchone()['id']
        self._set_guardian_session()
        with patch('routes.auth.get_db', return_value=self.db):
            response = self.client.post('/portal-encarregado', data={
                'csrf_token': 'test-csrf-token',
                'acao': 'reclamacao_message',
                'rec_id': str(complaint_id),
                'mensagem': 'Tentativa de acesso',
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.db.execute('SELECT COUNT(*) FROM complaint_messages').fetchone()[0],
            0,
        )

    def test_secretary_reply_and_forward_are_visible_to_director(self):
        self.db.execute('''
            INSERT INTO reclamacoes_encarregados (
                student_id, guardian_id, nome_encarregado, contacto, mensagem,
                categoria, assunto, estado
            ) VALUES (10, 1, 'Encarregada Teste', '', 'Mensagem inicial',
                      'Atendimento', 'Pedido de apoio', 'Nova')
        ''')
        complaint_id = self.db.execute(
            'SELECT id FROM reclamacoes_encarregados'
        ).fetchone()['id']
        self._set_staff_session('secretary', 1)

        with patch('routes.secretary.get_db', return_value=self.db):
            inbox = self.client.get('/secretary/reclamacoes')
            reply = self.client.post('/secretary/reclamacoes', data={
                'csrf_token': 'test-csrf-token',
                'rec_id': str(complaint_id),
                'action': 'reply',
                'message_key': f'complaint:{complaint_id}:reply-test-001',
                'resposta_escola': 'A Secretaria está a acompanhar o pedido.',
            })
            duplicate_reply = self.client.post('/secretary/reclamacoes', data={
                'csrf_token': 'test-csrf-token',
                'rec_id': str(complaint_id),
                'action': 'reply',
                'message_key': f'complaint:{complaint_id}:reply-test-001',
                'resposta_escola': 'A Secretaria está a acompanhar o pedido.',
            })
            forward = self.client.post('/secretary/reclamacoes', data={
                'csrf_token': 'test-csrf-token',
                'rec_id': str(complaint_id),
                'action': 'forward',
                'forward_reason': 'Necessita de decisão da Direcção.',
            })

        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(reply.status_code, 302)
        self.assertEqual(duplicate_reply.status_code, 302)
        self.assertEqual(forward.status_code, 302)
        self.assertEqual(
            self.db.execute('SELECT COUNT(*) FROM complaint_messages').fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.execute(
                'SELECT forwarded_to_director FROM reclamacoes_encarregados WHERE id = ?',
                (complaint_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM complaint_notifications WHERE kind = 'forwarded'"
            ).fetchone()[0],
            1,
        )

        self._set_staff_session('director', 2)
        with patch('routes.director.get_db', return_value=self.db):
            director_inbox = self.client.get('/director/reclamacoes')
            director_reply = self.client.post('/director/reclamacoes', data={
                'csrf_token': 'test-csrf-token',
                'rec_id': str(complaint_id),
                'action': 'reply',
                'message_key': f'complaint:{complaint_id}:director-reply-test',
                'resposta': 'A Direcção analisou o pedido.',
            })
        self.assertEqual(director_inbox.status_code, 200)
        self.assertIn(b'Pedido de apoio', director_inbox.data)
        self.assertEqual(director_reply.status_code, 302)
        self.assertEqual(
            self.db.execute(
                'SELECT COUNT(*) FROM complaint_messages WHERE sender_type = ?',
                ('director',),
            ).fetchone()[0],
            1,
        )

    def test_pedagogical_role_cannot_access_director_complaints(self):
        self._set_staff_session('pedagogico_1', 2)
        with patch('routes.director.get_db', return_value=self.db):
            response = self.client.get('/director/reclamacoes')
        self.assertEqual(response.status_code, 302)


if __name__ == '__main__':
    unittest.main()
