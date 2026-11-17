import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'teacher-grades-test-secret')

from app import app


class TeacherGradeWorkflowTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='teacher-grades-test-secret')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        with open('database/schema.sql', encoding='utf-8') as schema:
            self.db.executescript(schema.read())
        self.db.executescript('''
            INSERT INTO classes (id, numero, nivel) VALUES
                (8, 8, 'Secundário'), (12, 12, 'Secundário');
            INSERT INTO academic_years (id, ano) VALUES (1, 2026);
            INSERT INTO subjects (id, nome, codigo) VALUES
                (1, 'Matemática', 'MAT'), (2, 'Física', 'FIS');
            INSERT INTO users (id, code, nome, email, password_hash, role)
            VALUES
                (7, 'PROF-7', 'Professor Teste', 'prof@example.test', 'hash', 'teacher'),
                (8, 'PROF-8', 'Outro Professor', 'outro@example.test', 'hash', 'teacher');
            INSERT INTO turmas (
                id, nome, classe_id, ano_lectivo_id, turno, curso,
                capacidade_maxima, vagas_disponiveis
            ) VALUES
                (1, '12ª A', 12, 1, 'Manhã', 'Diurno', 35, 33),
                (2, '8ª A', 8, 1, 'Tarde', 'Diurno', 35, 34);
            INSERT INTO students (
                id, code, nome, bi_cedula, data_nascimento,
                encarregado_contacto_1, classe_ingresso, turma_id,
                ano_lectivo_id, turno, curso, data_matricula, funcionario_id
            ) VALUES
                (1, 'ALU-1', 'Aluno da Turma', 'BI-1', '2008-01-01',
                 '840000000', 12, 1, 1, 'Manhã', 'Diurno', '2026-01-01', 8),
                (2, 'ALU-2', 'Segundo Aluno', 'BI-2', '2008-01-01',
                 '840000001', 12, 1, 1, 'Manhã', 'Diurno', '2026-01-01', 8),
                (3, 'ALU-3', 'Aluno de Outra Turma', 'BI-3', '2010-01-01',
                 '840000002', 8, 2, 1, 'Tarde', 'Diurno', '2026-01-01', 8);
            INSERT INTO teacher_assignments (id, teacher_id, turma_id, subject_id)
            VALUES (1, 7, 1, 1);
        ''')
        self.db.commit()
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'teacher'
            browser_session['user_id'] = 7

    def tearDown(self):
        self.db.close()

    def _csrf(self):
        self.client.get('/')
        with self.client.session_transaction() as browser_session:
            return browser_session['_csrf_token']

    def _grade_form(self, **overrides):
        values = {
            'csrf_token': self._csrf(),
            'student_id': ['1', '2'],
            'av1_1': '10',
            'av2_1': '12',
            'av3_1': '14',
            'av4_1': '16',
            'exame_1': '15',
            'fj_1': '1',
            'fnj_1': '2',
            'comentario_1': 'Bom progresso',
            'av1_2': '8',
            'av2_2': '10',
            'av3_2': '12',
            'av4_2': '14',
            'exame_2': '10',
            'fj_2': '0',
            'fnj_2': '0',
            'comentario_2': '',
        }
        values.update(overrides)
        return values

    def test_assigned_teacher_submits_exam_grades_for_class_12(self):
        with (
            patch('routes.teacher.get_db', return_value=self.db),
            patch('routes.teacher.log_audit'),
        ):
            response = self.client.post(
                '/teacher/grades/1/1',
                data=self._grade_form(),
            )

        self.assertEqual(response.status_code, 302)
        rows = self.db.execute('''
            SELECT student_id, media_trimestral, exame, media_final,
                   total_faltas, status
            FROM grades ORDER BY student_id
        ''').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(tuple(rows[0]), (1, 13.0, 15.0, 13.8, 3, 'Submetida'))
        self.assertEqual(tuple(rows[1]), (2, 11.0, 10.0, 10.6, 0, 'Submetida'))

    def test_rejects_out_of_range_scores_and_students_outside_assigned_class(self):
        invalid_forms = (
            self._grade_form(av1_1='20.1'),
            self._grade_form(student_id=['1', '3']),
        )
        with (
            patch('routes.teacher.get_db', return_value=self.db),
            patch('routes.teacher.log_audit'),
        ):
            for form in invalid_forms:
                response = self.client.post('/teacher/grades/1/1', data=form)
                self.assertEqual(response.status_code, 302)

        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM grades').fetchone()[0], 0)

    def test_unassigned_teacher_cannot_launch_grades(self):
        with self.client.session_transaction() as browser_session:
            browser_session['user_id'] = 8
        with patch('routes.teacher.get_db', return_value=self.db):
            response = self.client.get('/teacher/grades/1/1')

        self.assertEqual(response.status_code, 302)

    def test_inactive_students_are_not_available_for_grading(self):
        self.db.execute('UPDATE students SET ativo = 0 WHERE id = 2')
        self.db.commit()
        with (
            patch('routes.teacher.get_db', return_value=self.db),
            patch('routes.teacher.log_audit'),
        ):
            page = self.client.get('/teacher/grades/1/1')
            response = self.client.post(
                '/teacher/grades/1/1',
                data=self._grade_form(),
            )

        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Aluno da Turma', page.data)
        self.assertNotIn(b'Segundo Aluno', page.data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM grades').fetchone()[0], 0)

    def test_unassigned_teacher_cannot_read_official_grade_sheet(self):
        with self.client.session_transaction() as browser_session:
            browser_session['user_id'] = 8
        with patch('routes.teacher.get_db', return_value=self.db):
            response = self.client.get('/teacher/pauta-oficial/1/1')
        self.assertEqual(response.status_code, 302)

    def test_submitted_grade_sheet_cannot_be_submitted_again(self):
        self.db.execute('''
            INSERT INTO grades (
                student_id, subject_id, turma_id, trimestre, status
            ) VALUES (1, 1, 1, 1, 'Submetida')
        ''')
        self.db.commit()
        with (
            patch('routes.teacher.get_db', return_value=self.db),
            patch('routes.teacher.log_audit'),
        ):
            response = self.client.post(
                '/teacher/grades/1/1',
                data=self._grade_form(),
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM grades').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
