import unittest

from scripts.seed_demo_data import (
    COMPLAINT_STATES,
    EXPECTED_CONFIRMATION,
    _insert_student,
    build_demo_credentials,
    expected_turma_specs,
    _curriculum_for,
    deletion_order,
    student_identity,
    synthetic_name,
    validate_target_values,
)


class PlaceholderCheckingCursor:
    lastrowid = 101

    def execute(self, statement, params=None):
        if params is not None:
            self.last_statement = statement
            self.last_params = params
            if statement.count('?') != len(params):
                raise AssertionError(
                    f'{statement.count("?")} placeholders for {len(params)} values'
                )


class DemoDataSeedTests(unittest.TestCase):
    def test_teacher_assignment_plan_matches_area_curricula(self):
        specs = expected_turma_specs()
        self.assertEqual(
            sum(len(_curriculum_for(spec)) for spec in specs),
            108,
        )

    def test_seed_plan_creates_fifteen_distinct_turmas_for_classes_eight_to_twelve(self):
        specs = expected_turma_specs()

        self.assertEqual(len(specs), 15)
        self.assertEqual(len({spec['name'] for spec in specs}), 15)
        self.assertEqual(
            {number: sum(spec['class_number'] == number for spec in specs)
             for number in range(8, 13)},
            {number: 3 for number in range(8, 13)},
        )
        self.assertTrue(all(spec['capacity'] > 20 for spec in specs))
        self.assertTrue(all(spec['area'] is None for spec in specs if spec['class_number'] < 10))
        self.assertTrue(all(spec['area'] in 'ABC' for spec in specs if spec['class_number'] >= 10))

    def test_student_demo_identifiers_are_unique_and_non_routable(self):
        identities = [student_identity(index) for index in range(1, 301)]

        self.assertEqual(len({row['code'] for row in identities}), 300)
        self.assertEqual(len({row['bi'] for row in identities}), 300)
        self.assertEqual(len({row['guardian_email'] for row in identities}), 300)
        self.assertTrue(all(row['guardian_email'].endswith('.test') for row in identities))
        self.assertTrue(all(row['contact'].startswith('DEMO-') for row in identities))

    def test_staff_and_guardian_credentials_are_unique_and_match_student_records(self):
        credentials = build_demo_credentials()
        passwords = [account['password'] for account in credentials]
        guardians = [account for account in credentials if account['type'] == 'guardian']

        self.assertEqual(len(credentials), 309)
        self.assertEqual(len(set(passwords)), len(passwords))
        self.assertEqual(len(guardians), 300)
        for index, account in enumerate(guardians, start=1):
            self.assertEqual(account['student_code'], student_identity(index)['code'])
            self.assertEqual(account['email'], student_identity(index)['guardian_email'])
            self.assertEqual(account['name'], synthetic_name(index, offset=1))

    def test_student_insert_has_one_value_for_each_sql_placeholder(self):
        cursor = PlaceholderCheckingCursor()
        _insert_student(
            cursor,
            1,
            expected_turma_specs()[0],
            turma_id=12,
            year_id=2026,
            secretary_id=3,
            guardian_id=19,
            guardian_name=synthetic_name(1, offset=1),
        )

    def test_cleanup_order_deletes_children_before_their_parents(self):
        result = deletion_order(
            {'students', 'guardians', 'guardian_students', 'reclamacoes_encarregados'},
            (
                ('guardian_students', 'guardians'),
                ('guardian_students', 'students'),
                ('reclamacoes_encarregados', 'students'),
            ),
        )

        self.assertLess(result.index('guardian_students'), result.index('guardians'))
        self.assertLess(result.index('guardian_students'), result.index('students'))
        self.assertLess(result.index('reclamacoes_encarregados'), result.index('students'))

    def test_target_requires_both_explicit_development_confirmations(self):
        validate_target_values(
            'neondb',
            'public',
            'ep-demo.neon.tech',
            EXPECTED_CONFIRMATION,
            'neondb',
        )
        with self.assertRaises(RuntimeError):
            validate_target_values(
                'sige_prod',
                'public',
                'ep-prod.neon.tech',
                EXPECTED_CONFIRMATION,
                'neondb',
            )
        with self.assertRaises(RuntimeError):
            validate_target_values(
                'neondb',
                'public',
                'ep-demo.neon.tech',
                '',
                'neondb',
            )
        with self.assertRaises(RuntimeError):
            validate_target_values(
                'neondb',
                'public',
                'localhost',
                EXPECTED_CONFIRMATION,
                'neondb',
            )

    def test_complaint_seed_covers_every_supported_status(self):
        self.assertEqual(
            set(COMPLAINT_STATES),
            {'Nova', 'Em Tratamento', 'Respondida', 'Resolvida', 'Reaberta'},
        )


if __name__ == '__main__':
    unittest.main()
