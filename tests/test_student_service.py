import sqlite3
import unittest

from services.student_service import atribuir_turma_automatica


class AutomaticClassAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE classes (id INTEGER PRIMARY KEY, numero INTEGER NOT NULL);
            CREATE TABLE turmas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                sala TEXT,
                classe_id INTEGER NOT NULL,
                ano_lectivo_id INTEGER NOT NULL,
                turno TEXT NOT NULL,
                curso TEXT NOT NULL,
                capacidade_maxima INTEGER NOT NULL,
                vagas_disponiveis INTEGER NOT NULL,
                area_curricular TEXT
            );
            INSERT INTO classes (id, numero) VALUES (8, 8), (10, 10), (11, 11), (12, 12);
        ''')

    def tearDown(self):
        self.db.close()

    def test_reuses_first_compatible_turma_and_reserves_one_place(self):
        cursor = self.db.cursor()
        cursor.execute('''
            INSERT INTO turmas (
                nome, classe_id, ano_lectivo_id, turno, curso,
                capacidade_maxima, vagas_disponiveis, area_curricular
            ) VALUES ('8ª A', 8, 1, 'Manhã', 'Diurno', 35, 1, NULL)
        ''')
        turma_id = cursor.lastrowid

        result = atribuir_turma_automatica(self.db, 8, 1, 'Manhã', capacidade_padrao=35)

        self.assertEqual(result, turma_id)
        cursor.execute('SELECT vagas_disponiveis FROM turmas WHERE id = ?', (turma_id,))
        self.assertEqual(cursor.fetchone()['vagas_disponiveis'], 0)

    def test_first_cycle_group_codes_continue_after_z(self):
        cursor = self.db.cursor()
        for index in range(1, 27):
            code = chr(64 + index)
            cursor.execute('''
                INSERT INTO turmas (
                    nome, classe_id, ano_lectivo_id, turno, curso,
                    capacidade_maxima, vagas_disponiveis
                ) VALUES (?, 8, 1, 'Tarde', 'Diurno', 1, 0)
            ''', (f'8ª {code}',))

        turma_id = atribuir_turma_automatica(self.db, 8, 1, 'Tarde')

        cursor.execute('SELECT nome FROM turmas WHERE id = ?', (turma_id,))
        self.assertEqual(cursor.fetchone()['nome'], '8ª AA')

    def test_second_cycle_uses_area_subgroup_and_configured_capacity(self):
        turma_id = atribuir_turma_automatica(
            self.db,
            10,
            1,
            'Manhã',
            area_curricular='B',
            capacidade_padrao=32,
        )
        cursor = self.db.cursor()
        cursor.execute(
            'SELECT nome, area_curricular, capacidade_maxima, vagas_disponiveis FROM turmas WHERE id = ?',
            (turma_id,),
        )
        turma = cursor.fetchone()

        self.assertEqual(turma['nome'], '10ª B01')
        self.assertEqual(turma['area_curricular'], 'B')
        self.assertEqual(turma['capacidade_maxima'], 32)
        self.assertEqual(turma['vagas_disponiveis'], 31)

    def test_areas_a_b_and_c_are_separate_for_classes_10_to_12(self):
        cursor = self.db.cursor()
        for class_number in (10, 11, 12):
            for area in ('A', 'B', 'C'):
                turma_id = atribuir_turma_automatica(
                    self.db,
                    class_number,
                    1,
                    'Manhã',
                    area_curricular=area,
                    capacidade_padrao=24,
                )
                cursor.execute('''
                    SELECT nome, area_curricular, capacidade_maxima, vagas_disponiveis
                    FROM turmas WHERE id = ?
                ''', (turma_id,))
                turma = cursor.fetchone()
                self.assertEqual(turma['nome'], f'{class_number}ª {area}01')
                self.assertEqual(turma['area_curricular'], area)
                self.assertEqual(turma['capacidade_maxima'], 24)
                self.assertEqual(turma['vagas_disponiveis'], 23)

    def test_rejects_night_mode_and_missing_second_cycle_area(self):
        with self.assertRaises(ValueError):
            atribuir_turma_automatica(self.db, 8, 1, 'Noite')
        with self.assertRaises(ValueError):
            atribuir_turma_automatica(self.db, 10, 1, 'Manhã')


if __name__ == '__main__':
    unittest.main()
