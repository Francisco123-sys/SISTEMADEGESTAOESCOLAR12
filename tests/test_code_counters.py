import sqlite3
import unittest
from unittest.mock import patch

from services.student_service import gerar_codigo_unico


class CodeCounterTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE code_counters (counter_key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE students (code TEXT UNIQUE);
            CREATE TABLE payments (code TEXT UNIQUE);
            CREATE TABLE users (code TEXT UNIQUE);
            INSERT INTO students (code) VALUES ('ALU-000004');
            INSERT INTO payments (code) VALUES ('REC-000009');
        ''')

    def tearDown(self):
        self.db.close()

    def test_counter_continues_after_existing_codes(self):
        with patch('services.student_service.get_db', return_value=self.db):
            first = gerar_codigo_unico('ALU', 'students')
            self.db.execute('INSERT INTO students (code) VALUES (?)', (first,))
            second = gerar_codigo_unico('ALU', 'students')
            receipt = gerar_codigo_unico('REC', 'payments')

        self.assertEqual(first, 'ALU-000005')
        self.assertEqual(second, 'ALU-000006')
        self.assertEqual(receipt, 'REC-000010')

    def test_table_identifier_is_allowlisted(self):
        with patch('services.student_service.get_db', return_value=self.db):
            with self.assertRaises(ValueError):
                gerar_codigo_unico('ALU', 'students; DROP TABLE users')


if __name__ == '__main__':
    unittest.main()
