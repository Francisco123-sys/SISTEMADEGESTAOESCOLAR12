import os
import sqlite3
import unittest
from unittest.mock import patch

from reportlab.platypus import Table

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
                encarregado_nome TEXT, encarregado_contacto_principal TEXT,
                encarregado_contacto_1 TEXT, ativo INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE guardians (
                id INTEGER PRIMARY KEY, nome TEXT, contacto TEXT, ativo INTEGER
            );
            CREATE TABLE guardian_students (
                guardian_id INTEGER, student_id INTEGER, principal INTEGER, created_at TEXT
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
        self.db.execute(
            "INSERT INTO guardians VALUES (1, 'Encarregado Principal', '841111111', 1)"
        )
        self.db.execute(
            "INSERT INTO guardian_students VALUES (1, 1, 1, '2026-01-01')"
        )
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

    def test_official_pdf_uses_guardian_details_and_institutional_columns(self):
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'chefe_secretaria'

        with (
            patch('routes.secretary.get_db', return_value=self.db),
            patch('routes.secretary.Table', wraps=Table) as table_factory,
        ):
            response = self.client.get('/secretary/turmas/1/lista-pdf/oficial')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertTrue(response.data.startswith(b'%PDF-'))
        self.assertGreaterEqual(response.data.count(b'/Type /Page'), 2)

        table_data = table_factory.call_args.args[0]
        headers = [cell.getPlainText() for cell in table_data[0]]
        self.assertEqual(
            headers,
            ['N.º', 'Nome completo', 'Processo', 'Sexo', 'Nasc.', 'Encarregado', 'Contacto'],
        )
        first_student = table_data[1]
        self.assertEqual(first_student[1].getPlainText(), 'Aluno com nome extenso 1')
        self.assertEqual(first_student[5].getPlainText(), 'Encarregado Principal')
        self.assertEqual(first_student[6].getPlainText(), '841111111')
        self.assertNotIn('BI/Cédula', headers)


if __name__ == '__main__':
    unittest.main()
