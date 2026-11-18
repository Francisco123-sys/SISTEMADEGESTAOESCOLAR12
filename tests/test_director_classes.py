import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app


class DirectorClassManagementTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE academic_years (id INTEGER PRIMARY KEY, ano INTEGER, ativo INTEGER);
            CREATE TABLE classes (id INTEGER PRIMARY KEY, numero INTEGER);
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, nome TEXT, role TEXT, ativo INTEGER
            );
            CREATE TABLE turmas (
                id INTEGER PRIMARY KEY,
                nome TEXT,
                sala TEXT,
                turno TEXT,
                curso TEXT,
                capacidade_maxima INTEGER,
                vagas_disponiveis INTEGER,
                area_curricular TEXT,
                diretor_turma_id INTEGER,
                classe_id INTEGER,
                ano_lectivo_id INTEGER
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY, turma_id INTEGER, code TEXT, nome TEXT,
                ativo INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE subjects (id INTEGER PRIMARY KEY, nome TEXT);
            CREATE TABLE grades (
                id INTEGER PRIMARY KEY, student_id INTEGER, subject_id INTEGER,
                turma_id INTEGER, trimestre INTEGER, av1 REAL, av2 REAL,
                av3 REAL, av4 REAL, media_trimestral REAL, exame REAL,
                media_final REAL
            );
            INSERT INTO academic_years VALUES (1, 2026, 1);
            INSERT INTO classes VALUES (8, 8), (10, 10);
            INSERT INTO users VALUES (9, 'Professor de Teste', 'teacher', 1);
            INSERT INTO turmas VALUES
                (1, '8ª A', 'Sala 01', 'Manhã', 'Diurno', 35, 33, NULL, NULL, 8, 1),
                (2, '8ª B', 'Sala 02', 'Tarde', 'Diurno', 30, 30, NULL, NULL, 8, 1),
                (3, '8ª C', 'Sala 03', 'Manhã', 'À Distância', 25, 25, NULL, NULL, 8, 1),
                (4, '10ª A01', 'Sala 04', 'Manhã', 'Diurno', 30, 29, 'A', NULL, 10, 1),
                (5, '10ª B01', 'Sala 05', 'Tarde', 'Diurno', 30, 30, 'B', NULL, 10, 1),
                (6, '10ª C01', 'Sala 06', 'Manhã', 'Diurno', 30, 0, 'C', NULL, 10, 1);
            INSERT INTO students (id, turma_id, code, nome) VALUES
                (1, 1, 'ALU-001', 'Aluno Um'),
                (2, 1, 'ALU-002', 'Aluno Dois');
            INSERT INTO subjects (id, nome) VALUES (1, 'Matemática');
            INSERT INTO grades (
                id, student_id, subject_id, turma_id, trimestre,
                av1, av2, av3, av4, media_trimestral, exame, media_final
            ) VALUES (1, 1, 1, 1, 1, 10, 11, 12, 13, 11.5, NULL, 11.5);
        ''')
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'director'
            browser_session['user_id'] = 1
            browser_session['_csrf_token'] = 'test-csrf-token'

    def tearDown(self):
        self.db.close()

    def test_dashboard_indicators_use_database_values(self):
        with patch('routes.director.get_db', return_value=self.db):
            response = self.client.get('/director/turmas')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'8\xc2\xaa A', response.data)
        self.assertIn(b'90', response.data)
        self.assertIn('À Distância'.encode(), response.data)

    def test_dashboard_filters_by_shift_and_class(self):
        with patch('routes.director.get_db', return_value=self.db):
            response = self.client.get('/director/turmas?turno=Tarde&classe=8')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'8\xc2\xaa B', response.data)
        self.assertNotIn(b'8\xc2\xaa A', response.data)
        self.assertIn(b'>30<', response.data)

    def test_dashboard_filters_by_area_and_available_capacity(self):
        with patch('routes.director.get_db', return_value=self.db):
            response = self.client.get('/director/turmas?classe=10&area=C&estado=com_vagas')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'10\xc2\xaa C01', response.data)
        self.assertNotIn(b'10\xc2\xaa A01', response.data)
        self.assertNotIn(b'10\xc2\xaa B01', response.data)
        self.assertIn(b'>1<', response.data)

    def test_grade_rectification_filters_progressively_and_requires_reason(self):
        with patch('routes.director.get_db', return_value=self.db):
            initial = self.client.get('/director/rectificar-notas')
            filtered = self.client.get(
                '/director/rectificar-notas'
                '?classe_id=8&turma_id=1&subject_id=1&trimestre=1&student_id=1'
            )
            missing_reason = self.client.post('/director/rectificar-notas', data={
                'csrf_token': 'test-csrf-token',
                'grade_id': '1',
                'av1': '15',
                'av2': '15',
                'av3': '15',
                'av4': '15',
                'exame': '',
                'motivo': '',
                'classe_id': '8',
                'turma_id': '1',
                'subject_id': '1',
                'trimestre': '1',
                'student_id': '1',
            })

        self.assertEqual(initial.status_code, 200)
        self.assertIn(b'Rectifica', initial.data)
        self.assertEqual(filtered.status_code, 200)
        self.assertIn('Aluno Um'.encode(), filtered.data)
        self.assertEqual(missing_reason.status_code, 302)
        row = self.db.execute(
            'SELECT av1, av2, av3, av4 FROM grades WHERE id = 1'
        ).fetchone()
        self.assertEqual(tuple(row), (10.0, 11.0, 12.0, 13.0))

    def test_grade_rectification_updates_and_logs_old_and_new_values(self):
        with (
            patch('routes.director.get_db', return_value=self.db),
            patch('routes.director.log_audit') as audit,
        ):
            response = self.client.post('/director/rectificar-notas', data={
                'csrf_token': 'test-csrf-token',
                'grade_id': '1',
                'av1': '14',
                'av2': '15',
                'av3': '16',
                'av4': '17',
                'exame': '',
                'motivo': 'Erro de transcrição confirmado pelo documento',
                'classe_id': '8',
                'turma_id': '1',
                'subject_id': '1',
                'trimestre': '1',
                'student_id': '1',
            })

        self.assertEqual(response.status_code, 302)
        row = self.db.execute(
            'SELECT av1, av2, av3, av4, media_trimestral, media_final '
            'FROM grades WHERE id = 1'
        ).fetchone()
        self.assertEqual(tuple(row), (14.0, 15.0, 16.0, 17.0, 15.5, 15.5))
        audit.assert_called_once()
        audit_text = ' '.join(str(argument) for argument in audit.call_args.args)
        audit_text += ' ' + ' '.join(
            str(argument) for argument in audit.call_args.kwargs.values()
        )
        self.assertIn('10.0', audit_text)
        self.assertIn('14.0', audit_text)
        self.assertIn('Erro de transcrição', audit_text)

    def test_capacity_cannot_be_lowered_below_enrollment(self):
        with patch('routes.director.get_db', return_value=self.db):
            response = self.client.post(
                '/director/turmas/1/configurar',
                data={
                    'csrf_token': 'test-csrf-token',
                    'capacidade_maxima': '1',
                    'diretor_turma_id': '9',
                },
            )

        self.assertEqual(response.status_code, 302)
        row = self.db.execute(
            'SELECT capacidade_maxima, diretor_turma_id FROM turmas WHERE id = 1'
        ).fetchone()
        self.assertEqual(tuple(row), (35, None))

    def test_capacity_and_director_assignment_are_saved(self):
        with (
            patch('routes.director.get_db', return_value=self.db),
            patch('routes.director.log_audit'),
        ):
            response = self.client.post(
                '/director/turmas/1/configurar',
                data={
                    'csrf_token': 'test-csrf-token',
                    'capacidade_maxima': '4',
                    'diretor_turma_id': '9',
                },
            )

        self.assertEqual(response.status_code, 302)
        row = self.db.execute(
            'SELECT capacidade_maxima, vagas_disponiveis, diretor_turma_id FROM turmas WHERE id = 1'
        ).fetchone()
        self.assertEqual(tuple(row), (4, 2, 9))


if __name__ == '__main__':
    unittest.main()
