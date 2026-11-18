import os
from pathlib import Path
import re
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app


class SecretaryStudentBrowsingTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[1] / 'database' / 'schema.sql'
        self.db.executescript(schema.read_text(encoding='utf-8'))
        self.db.execute(
            '''INSERT INTO users (code, nome, email, password_hash, role)
               VALUES ('SEC-TEST', 'Secretaria de Teste', 'secretaria@test.local',
                       'not-used', 'secretary')'''
        )
        self.db.execute(
            'INSERT INTO academic_years (ano, ativo) VALUES (2026, 1)'
        )
        self.db.executemany(
            'INSERT INTO classes (numero, nivel) VALUES (?, ?)',
            [(number, 'Secundário') for number in range(8, 13)],
        )
        class_ids = {
            row['numero']: row['id']
            for row in self.db.execute('SELECT id, numero FROM classes')
        }
        self.db.executemany('''
            INSERT INTO turmas (
                nome, sala, classe_id, ano_lectivo_id, turno, curso,
                capacidade_maxima, area_curricular
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
        ''', [
            ('8.ª A', 'Sala 01', class_ids[8], 'Manhã', 'Diurno', 15, 'A'),
            ('8.ª B', 'Sala 02', class_ids[8], 'Tarde', 'Diurno', 20, 'B'),
            ('9.ª A', 'Sala 03', class_ids[9], 'Manhã', 'Diurno', 20, 'A'),
        ])
        turma_ids = {
            row['nome']: row['id']
            for row in self.db.execute('SELECT id, nome FROM turmas')
        }
        for index in range(12):
            name = 'Ana de Teste' if index == 0 else f'Aluno {index:02}'
            self._insert_student(
                code=f'ALU-{index + 1:06}',
                name=name,
                turma_id=turma_ids['8.ª A'],
                bi=f'BI-TEST-{index + 1:03}',
                nee='Auditiva' if index == 0 else 'Nenhuma',
                isento=1 if index == 0 else 0,
            )
        self._insert_student(
            code='ALU-000020',
            name='Aluno da turma B',
            turma_id=turma_ids['8.ª B'],
            bi='BI-TEST-020',
        )
        self._insert_student(
            code='ALU-000021',
            name='Aluno da nona',
            turma_id=turma_ids['9.ª A'],
            bi='BI-TEST-021',
        )
        self._insert_student(
            code='ALU-000022',
            name='Matrícula inactiva',
            turma_id=turma_ids['8.ª A'],
            bi='BI-TEST-022',
            active=0,
        )
        self.inactive_student_id = self.db.execute(
            "SELECT id FROM students WHERE code = 'ALU-000022'"
        ).fetchone()['id']
        self.db.commit()
        self.class_ids = class_ids
        self.turma_ids = turma_ids
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = 1
            browser_session['_csrf_token'] = 'test-csrf-token'

    def tearDown(self):
        self.db.close()

    def _insert_student(
        self, code, name, turma_id, bi, nee='Nenhuma', isento=0, active=1
    ):
        self.db.execute('''
            INSERT INTO students (
                code, nome, bi_cedula, data_nascimento, sexo,
                encarregado_contacto_1, encarregado_nome,
                classe_ingresso, turma_id, ano_lectivo_id, turno, curso,
                data_matricula, isento_taxa, nee_tipo, ativo, funcionario_id
            ) VALUES (?, ?, ?, '2010-01-01', 'F', '840000001',
                      'Encarregado Teste', 8, ?, 1, 'Manhã', 'Diurno',
                      '2026-01-10', ?, ?, ?, 1)
        ''', (code, name, bi, turma_id, isento, nee, active))

    def _get(self, path, role='secretary'):
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = role
        with patch('routes.secretary.get_db', return_value=self.db):
            return self.client.get(path)

    def test_default_view_shows_only_class_cards_not_student_rows(self):
        response = self._get('/secretary/students')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('<header'), 1)
        self.assertIn('aria-label="Navegação da Secretaria"', html)
        self.assertIn('Matricular aluno', html)
        self.assertIn('student-browser-class-card', html)
        self.assertIn('8.ª Classe', html)
        self.assertIn('12.ª Classe', html)
        self.assertNotIn('Ana de Teste', html)
        self.assertNotIn('Aluno da nona', html)

    def test_class_selection_shows_only_its_turma_cards(self):
        response = self._get(
            f'/secretary/students?classe_id={self.class_ids[8]}'
            '&ano_lectivo_id=1'
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('8.ª A', html)
        self.assertIn('8.ª B', html)
        self.assertNotIn('9.ª A', html)
        self.assertNotIn('Ana de Teste', html)
        self.assertNotIn('Aluno da turma B', html)

    def test_turma_view_scopes_students_and_combines_filters(self):
        response = self._get(
            f'/secretary/students?classe_id={self.class_ids[8]}'
            f'&turma_id={self.turma_ids["8.ª A"]}&ano_lectivo_id=1'
            '&turno=Manh%C3%A3&modalidade=Diurno&area=A&especial=nee&q=Ana'
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Ana de Teste', html)
        self.assertNotIn('Aluno 01', html)
        self.assertNotIn('Aluno da turma B', html)
        self.assertIn('Consultar perfil', html)
        self.assertIn('Declaração de frequência', html)

    def test_bi_search_and_turma_class_mismatch_do_not_leak_other_students(self):
        response = self._get(
            f'/secretary/students?classe_id={self.class_ids[8]}'
            f'&turma_id={self.turma_ids["8.ª A"]}&ano_lectivo_id=1'
            '&q=BI-TEST-003'
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Aluno 02', html)
        self.assertNotIn('Aluno 01', html)

        mismatch_response = self._get(
            f'/secretary/students?classe_id={self.class_ids[8]}'
            f'&turma_id={self.turma_ids["9.ª A"]}&ano_lectivo_id=1'
        )
        self.assertEqual(mismatch_response.status_code, 302)

    def test_pagination_keeps_scope_filters_and_page_size(self):
        turma_id = self.turma_ids['8.ª A']
        class_id = self.class_ids[8]
        response = self._get(
            f'/secretary/students?classe_id={class_id}&turma_id={turma_id}'
            '&ano_lectivo_id=1&q=ALU-&per_page=10'
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('12 registos correspondem', html)
        self.assertIn('Página 1 de 2', html)
        self.assertIn('per_page=10', html)
        self.assertIn(f'classe_id={class_id}', html)
        self.assertIn(f'turma_id={turma_id}', html)

        second_page = self._get(
            f'/secretary/students?classe_id={class_id}&turma_id={turma_id}'
            '&ano_lectivo_id=1&q=ALU-&per_page=10&page=2'
        )
        second_html = second_page.get_data(as_text=True)
        self.assertIn('Página 2 de 2', second_html)
        student_rows = re.search(r'<tbody>(.*?)</tbody>', second_html, re.S).group(1)
        self.assertIn('ALU-000012', student_rows)
        self.assertNotIn('ALU-000002', student_rows)

    def test_profile_shows_student_information_and_returns_to_real_class_ids(self):
        response = self._get('/secretary/students/1/profile')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Perfil do aluno', html)
        self.assertIn('Encarregado Teste', html)
        self.assertIn('BI-TEST-001', html)
        self.assertIn(f'classe_id={self.class_ids[8]}', html)
        self.assertIn(f'turma_id={self.turma_ids["8.ª A"]}', html)

    def test_inactive_students_are_not_exposed_to_secretary_profile(self):
        response = self._get(
            f'/secretary/students?classe_id={self.class_ids[8]}'
            f'&turma_id={self.turma_ids["8.ª A"]}&ano_lectivo_id=1'
            '&situacao=inactivos'
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Matrícula inactiva', html)
        self.assertIn('requer perfil autorizado', html)

        inactive_response = self._get(
            f'/secretary/students/{self.inactive_student_id}/profile',
            role='secretary',
        )
        self.assertEqual(inactive_response.status_code, 302)

        chief_response = self._get(
            f'/secretary/students?classe_id={self.class_ids[8]}'
            f'&turma_id={self.turma_ids["8.ª A"]}&ano_lectivo_id=1'
            '&situacao=inactivos',
            role='chefe_secretaria',
        )
        self.assertEqual(chief_response.status_code, 200)
        self.assertIn('Matrícula inactiva', chief_response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
