import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app


class ClassListPdfTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE classes (id INTEGER PRIMARY KEY, numero INTEGER);
            CREATE TABLE academic_years (id INTEGER PRIMARY KEY, ano INTEGER);
            CREATE TABLE users (id INTEGER PRIMARY KEY, nome TEXT);
            CREATE TABLE turmas (
                id INTEGER PRIMARY KEY, nome TEXT, sala TEXT, turno TEXT, curso TEXT,
                area_curricular TEXT, classe_id INTEGER, ano_lectivo_id INTEGER,
                diretor_turma_id INTEGER
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY, turma_id INTEGER, nome TEXT, code TEXT,
                bi_cedula TEXT, sexo TEXT, data_nascimento TEXT,
                encarregado_contacto_principal TEXT, encarregado_contacto_1 TEXT
            );
            INSERT INTO classes VALUES (1, 8);
            INSERT INTO academic_years VALUES (1, 2026);
            INSERT INTO turmas VALUES (1, '8ª A', 'Sala 01', 'Manhã', 'Diurno', NULL, 1, 1, NULL);
        ''')
        self.db.executemany('''
            INSERT INTO students (id, turma_id, nome, code, bi_cedula, sexo, data_nascimento,
                                  encarregado_contacto_principal, encarregado_contacto_1)
            VALUES (?, 1, ?, ?, ?, 'M', '2010-01-01', '841234567', '841234567')
        ''', [
            (index, f'Aluno com nome extenso {index}', f'ALU-{index}', f'BI-{index}')
            for index in range(1, 81)
        ])
        self.db.commit()
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = 1

    def tearDown(self):
        self.db.close()

    def test_vitrine_pdf_paginates_many_students(self):
        with patch('routes.secretary.get_db', return_value=self.db):
            response = self.client.get('/secretary/turmas/1/lista-pdf/vitrine')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertTrue(response.data.startswith(b'%PDF-'))
        self.assertGreaterEqual(response.data.count(b'/Type /Page'), 2)

    def test_official_list_is_restricted_to_authorized_secretariat(self):
        with patch('routes.secretary.get_db', return_value=self.db):
            response = self.client.get('/secretary/turmas/1/lista-pdf/oficial')

        self.assertEqual(response.status_code, 302)


if __name__ == '__main__':
    unittest.main()
