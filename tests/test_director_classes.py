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
            CREATE TABLE students (id INTEGER PRIMARY KEY, turma_id INTEGER);
            INSERT INTO academic_years VALUES (1, 2026, 1);
            INSERT INTO classes VALUES (8, 8);
            INSERT INTO users VALUES (9, 'Professor de Teste', 'teacher', 1);
            INSERT INTO turmas VALUES
                (1, '8ª A', 'Sala 01', 'Manhã', 'Diurno', 35, 33, NULL, NULL, 8, 1),
                (2, '8ª B', 'Sala 02', 'Tarde', 'Diurno', 30, 30, NULL, NULL, 8, 1),
                (3, '8ª C', 'Sala 03', 'Manhã', 'À Distância', 25, 25, NULL, NULL, 8, 1);
            INSERT INTO students VALUES (1, 1), (2, 1);
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
