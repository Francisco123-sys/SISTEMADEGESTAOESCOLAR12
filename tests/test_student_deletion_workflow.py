import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app


class StudentDeletionWorkflowTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE classes (id INTEGER PRIMARY KEY, numero INTEGER);
            CREATE TABLE academic_years (
                id INTEGER PRIMARY KEY, ano INTEGER, ativo INTEGER
            );
            CREATE TABLE subjects (id INTEGER PRIMARY KEY, nome TEXT);
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, nome TEXT, role TEXT, ativo INTEGER
            );
            CREATE TABLE turmas (
                id INTEGER PRIMARY KEY, nome TEXT, sala TEXT, turno TEXT, curso TEXT,
                capacidade_maxima INTEGER, vagas_disponiveis INTEGER,
                area_curricular TEXT, classe_id INTEGER, ano_lectivo_id INTEGER,
                diretor_turma_id INTEGER
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY, code TEXT, nome TEXT, sexo TEXT,
                data_nascimento TEXT, foto_path TEXT, turma_id INTEGER,
                classe_ingresso INTEGER,
                ativo INTEGER NOT NULL DEFAULT 1, eliminado_em TIMESTAMP,
                eliminado_por INTEGER, encarregado_nome TEXT,
                encarregado_contacto_principal TEXT, encarregado_contacto_1 TEXT
            );
            CREATE TABLE guardians (
                id INTEGER PRIMARY KEY, nome TEXT, contacto TEXT, ativo INTEGER
            );
            CREATE TABLE guardian_students (
                guardian_id INTEGER, student_id INTEGER, principal INTEGER, created_at TEXT
            );
            CREATE TABLE student_deletion_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL,
                turma_id INTEGER NOT NULL, solicitado_por INTEGER NOT NULL,
                solicitado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, motivo TEXT,
                estado TEXT NOT NULL DEFAULT 'Pendente', decidido_por INTEGER,
                decidido_em TIMESTAMP, motivo_decisao TEXT
            );
            CREATE UNIQUE INDEX uq_student_deletion_pending
                ON student_deletion_requests(student_id) WHERE estado = 'Pendente';
            INSERT INTO classes VALUES (8, 8);
            INSERT INTO academic_years VALUES (1, 2026, 1);
            INSERT INTO subjects VALUES (1, 'Matemática');
            INSERT INTO users VALUES (1, 'Director de Teste', 'director', 1);
            INSERT INTO users VALUES (2, 'Secretaria de Teste', 'secretary', 1);
            INSERT INTO turmas VALUES
                (1, '8ª A', 'Sala 01', 'Manhã', 'Diurno', 4, 2, NULL, 8, 1, NULL);
            INSERT INTO students (
                id, code, nome, sexo, data_nascimento, turma_id, classe_ingresso, ativo,
                encarregado_nome, encarregado_contacto_principal
            ) VALUES
                (1, 'ALU-000001', 'Ana de Teste', 'F', '2010-01-01', 1, 8, 1, 'Encarregado Teste', '840000001'),
                (2, 'ALU-000002', 'Bruno de Teste', 'M', '2010-02-02', 1, 8, 1, 'Outro Encarregado', '840000002'),
                (3, 'ALU-000003', 'Aluno Inactivo', 'M', '2010-03-03', 1, 8, 0, NULL, NULL);
        ''')
        self.db.commit()
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = 2
            browser_session['_csrf_token'] = 'test-csrf-token'

    def tearDown(self):
        self.db.close()

    def _set_role(self, role, user_id):
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = role
            browser_session['user_id'] = user_id
            browser_session['_csrf_token'] = 'test-csrf-token'

    def _insert_pending_request(self, requester_id=2):
        self.db.execute('''
            INSERT INTO student_deletion_requests (
                student_id, turma_id, solicitado_por, motivo
            ) VALUES (1, 1, ?, 'Teste controlado')
        ''', (requester_id,))
        self.db.commit()
        return self.db.execute(
            'SELECT id FROM student_deletion_requests'
        ).fetchone()['id']

    def test_class_detail_is_scoped_and_calculates_active_student_indicators(self):
        with patch('routes.secretary.get_db', return_value=self.db):
            response = self.client.get('/secretary/turmas/1')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'8\xc2\xaa A', response.data)
        self.assertIn(b'2 de 4 lugares ocupados', response.data)
        self.assertIn(b'50%', response.data)
        self.assertIn(b'Ana de Teste', response.data)
        self.assertIn(b'Bruno de Teste', response.data)
        self.assertNotIn(b'Aluno Inactivo', response.data)
        self.assertIn(b'Encarregado Teste', response.data)

    def test_turma_management_renders_cards_linked_to_individual_class_page(self):
        with patch('routes.secretary.get_db', return_value=self.db):
            response = self.client.get('/secretary/turmas')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'secretary-turma-card', response.data)
        self.assertIn(b'href="/secretary/turmas/1"', response.data)
        self.assertIn(b'8\xc2\xaa A', response.data)

    def test_secretary_can_request_deletion_but_cannot_delete_student_or_duplicate_request(self):
        with (
            patch('routes.secretary.get_db', return_value=self.db),
            patch(
                'routes.secretary.log_audit',
                side_effect=lambda *args: self.db.commit(),
            ) as audit,
        ):
            response = self.client.post(
                '/secretary/students/1/deletion-request',
                data={'csrf_token': 'test-csrf-token', 'motivo': 'Pedido de teste'},
            )
            duplicate_response = self.client.post(
                '/secretary/students/1/deletion-request',
                data={'csrf_token': 'test-csrf-token'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(duplicate_response.status_code, 302)
        request_row = self.db.execute(
            'SELECT estado, solicitado_por, motivo FROM student_deletion_requests'
        ).fetchone()
        self.assertEqual(tuple(request_row), ('Pendente', 2, 'Pedido de teste'))
        self.assertEqual(self.db.execute(
            'SELECT ativo FROM students WHERE id = 1'
        ).fetchone()['ativo'], 1)
        self.assertEqual(audit.call_count, 1)

    def test_director_approval_soft_deletes_student_and_recalculates_vacancies(self):
        request_id = self._insert_pending_request()
        self._set_role('director', 1)

        with (
            patch('routes.director.get_db', return_value=self.db),
            patch(
                'routes.director.log_audit',
                side_effect=lambda *args, **kwargs: self.db.commit(),
            ) as audit,
        ):
            response = self.client.post(
                f'/director/student-deletion/{request_id}/decision',
                data={
                    'csrf_token': 'test-csrf-token',
                    'decision': 'approve',
                    'decision_note': 'Aprovado em teste',
                },
            )

        self.assertEqual(response.status_code, 302)
        student = self.db.execute(
            'SELECT ativo, eliminado_por FROM students WHERE id = 1'
        ).fetchone()
        request_row = self.db.execute(
            'SELECT estado, decidido_por, motivo_decisao FROM student_deletion_requests WHERE id = ?',
            (request_id,),
        ).fetchone()
        vacancies = self.db.execute(
            'SELECT vagas_disponiveis FROM turmas WHERE id = 1'
        ).fetchone()['vagas_disponiveis']
        self.assertEqual(tuple(student), (0, 1))
        self.assertEqual(tuple(request_row), ('Aprovado', 1, 'Aprovado em teste'))
        self.assertEqual(vacancies, 3)
        self.assertEqual(audit.call_count, 1)

    def test_rejection_keeps_student_active_and_vacancies_unchanged(self):
        request_id = self._insert_pending_request()
        self._set_role('director', 1)

        with (
            patch('routes.director.get_db', return_value=self.db),
            patch(
                'routes.director.log_audit',
                side_effect=lambda *args, **kwargs: self.db.commit(),
            ),
        ):
            response = self.client.post(
                f'/director/student-deletion/{request_id}/decision',
                data={'csrf_token': 'test-csrf-token', 'decision': 'reject'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.db.execute('SELECT ativo FROM students WHERE id = 1').fetchone()['ativo'],
            1,
        )
        self.assertEqual(
            self.db.execute(
                'SELECT estado FROM student_deletion_requests WHERE id = ?',
                (request_id,),
            ).fetchone()['estado'],
            'Rejeitado',
        )
        self.assertEqual(
            self.db.execute(
                'SELECT vagas_disponiveis FROM turmas WHERE id = 1'
            ).fetchone()['vagas_disponiveis'],
            2,
        )

    def test_director_cannot_approve_own_request_and_secretary_cannot_decide(self):
        request_id = self._insert_pending_request(requester_id=1)
        self._set_role('director', 1)
        with patch('routes.director.get_db', return_value=self.db):
            own_request_response = self.client.post(
                f'/director/student-deletion/{request_id}/decision',
                data={'csrf_token': 'test-csrf-token', 'decision': 'approve'},
            )

        self.assertEqual(own_request_response.status_code, 302)
        self.assertEqual(
            self.db.execute(
                'SELECT estado FROM student_deletion_requests WHERE id = ?',
                (request_id,),
            ).fetchone()['estado'],
            'Pendente',
        )
        self._set_role('secretary', 2)
        with patch('routes.director.get_db', return_value=self.db):
            secretary_response = self.client.post(
                f'/director/student-deletion/{request_id}/decision',
                data={'csrf_token': 'test-csrf-token', 'decision': 'approve'},
            )
        self.assertEqual(secretary_response.status_code, 302)
        self.assertEqual(
            self.db.execute(
                'SELECT estado FROM student_deletion_requests WHERE id = ?',
                (request_id,),
            ).fetchone()['estado'],
            'Pendente',
        )


if __name__ == '__main__':
    unittest.main()
