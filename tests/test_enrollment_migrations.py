import sqlite3
import unittest

from database.enrollment_migrations import migrate_enrollment_schema


class EnrollmentMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('''
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                bi_cedula TEXT UNIQUE NOT NULL
            )
        ''')
        self.db.execute("INSERT INTO students (id, bi_cedula) VALUES (1, 'OLD-BI')")

    def tearDown(self):
        self.db.close()

    def test_migration_is_additive_idempotent_and_preserves_existing_students(self):
        cursor = self.db.cursor()
        migrate_enrollment_schema(cursor, is_postgres=False)
        migrate_enrollment_schema(cursor, is_postgres=False)

        columns = {row[1] for row in cursor.execute('PRAGMA table_info(students)')}
        self.assertIn('distrito_nascimento', columns)
        self.assertIn('pais_residencia', columns)
        self.assertIn('documento_identificacao_numero', columns)
        self.assertEqual(
            cursor.execute('SELECT bi_cedula FROM students WHERE id = 1').fetchone()[0],
            'OLD-BI',
        )

    def test_document_number_is_unique_per_issuer_and_type_not_globally(self):
        cursor = self.db.cursor()
        migrate_enrollment_schema(cursor, is_postgres=False)
        cursor.executemany('''
            INSERT INTO students (
                id, bi_cedula, documento_identificacao_pais,
                documento_identificacao_tipo, documento_identificacao_numero
            ) VALUES (?, ?, ?, ?, ?)
        ''', [
            (2, 'EXT-1', 'Portugal', 'Passaporte', '12345'),
            (3, 'EXT-2', 'África do Sul', 'Passaporte', '12345'),
        ])

        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute('''
                INSERT INTO students (
                    id, bi_cedula, documento_identificacao_pais,
                    documento_identificacao_tipo, documento_identificacao_numero
                ) VALUES (4, 'EXT-3', 'Portugal', 'Passaporte', '12345')
            ''')


if __name__ == '__main__':
    unittest.main()
