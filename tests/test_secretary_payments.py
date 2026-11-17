import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'payment-workflow-test-secret')

from app import app


class SecretaryPaymentWorkflowTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='payment-workflow-test-secret')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        with open('database/schema.sql', encoding='utf-8') as schema:
            self.db.executescript(schema.read())
        self.db.execute('INSERT INTO academic_years (id, ano) VALUES (1, 2026)')
        self.db.execute('INSERT INTO classes (id, numero, nivel) VALUES (8, 8, "Secundário")')
        self.db.execute('''
            INSERT INTO users (id, code, nome, email, password_hash, role)
            VALUES (7, 'SEC-7', 'Secretaria Teste', 'secretaria@example.test', 'hash', 'secretary')
        ''')
        self.db.execute('''
            INSERT INTO turmas (
                id, nome, classe_id, ano_lectivo_id, turno, curso,
                capacidade_maxima, vagas_disponiveis
            ) VALUES (1, '8ª A', 8, 1, 'Manhã', 'Diurno', 35, 33)
        ''')
        self.db.executemany('''
            INSERT INTO students (
                id, code, nome, bi_cedula, data_nascimento,
                encarregado_contacto_1, classe_ingresso, turma_id,
                ano_lectivo_id, turno, curso, data_matricula, funcionario_id,
                isento_taxa
            )
            VALUES (?, ?, ?, ?, '2010-01-01', '840000000', 8, 1,
                    1, 'Manhã', 'Diurno', '2026-01-01', 7, ?)
        ''', (
            (1, 'ALU-1', 'Aluno Pagador', 'BI-1', 0),
            (2, 'ALU-2', 'Aluno Isento', 'BI-2', 1),
        ))
        self.db.commit()
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = 7

    def tearDown(self):
        self.db.close()

    def _csrf(self):
        self.client.get('/secretary/payments/new')
        with self.client.session_transaction() as browser_session:
            return browser_session['_csrf_token']

    def _payment_form(self, **overrides):
        fields = {
            'csrf_token': self._csrf(),
            'student_id': '1',
            'mes_pago': 'Abril',
            'valor_pago': '500.50',
            'banco_plataforma': 'Banco BCI',
            'referencia_talao': '123456789',
            'data_deposito': '2026-01-01',
        }
        fields.update(overrides)
        return fields

    def test_payment_creates_receipt_and_persists_reference_and_amount(self):
        with (
            patch('routes.secretary.get_db', return_value=self.db),
            patch('routes.secretary.log_audit'),
        ):
            response = self.client.post(
                '/secretary/payments/new',
                data=self._payment_form(),
            )
            self.assertEqual(response.status_code, 302)
            receipt = self.client.get(response.headers['Location'])

        self.assertEqual(receipt.status_code, 200)
        self.assertIn(b'ALU-1', receipt.data)
        self.assertIn(b'500.50', receipt.data)
        payment = self.db.execute(
            'SELECT referencia_talao, valor_pago, banco_plataforma FROM payments'
        ).fetchone()
        self.assertEqual(
            tuple(payment),
            ('123456789', 500.5, 'Banco BCI'),
        )

    def test_invalid_payments_do_not_create_receipt(self):
        bad_forms = (
            {'valor_pago': 'NaN'},
            {'valor_pago': '-1'},
            {'banco_plataforma': 'Banco Inventado'},
            {'referencia_talao': '１２３４５６７８９'},
            {'data_deposito': '2099-01-01'},
            {'student_id': '2'},
        )
        with (
            patch('routes.secretary.get_db', return_value=self.db),
            patch('routes.secretary.log_audit'),
        ):
            for overrides in bad_forms:
                response = self.client.post(
                    '/secretary/payments/new',
                    data=self._payment_form(**overrides),
                )
                self.assertEqual(response.status_code, 302)

        self.assertEqual(
            self.db.execute('SELECT COUNT(*) FROM payments').fetchone()[0],
            0,
        )

    def test_duplicate_reference_is_rejected(self):
        with (
            patch('routes.secretary.get_db', return_value=self.db),
            patch('routes.secretary.log_audit'),
        ):
            first = self.client.post(
                '/secretary/payments/new',
                data=self._payment_form(),
            )
            second = self.client.post(
                '/secretary/payments/new',
                data=self._payment_form(
                    student_id='1',
                    referencia_talao='123456789',
                ),
            )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            self.db.execute('SELECT COUNT(*) FROM payments').fetchone()[0],
            1,
        )

    def test_receipt_requires_secretariat_role_and_existing_payment(self):
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'teacher'
        with patch('routes.secretary.get_db', return_value=self.db):
            unauthorized = self.client.get('/secretary/receipt/1')
        self.assertEqual(unauthorized.status_code, 302)

        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
        with patch('routes.secretary.get_db', return_value=self.db):
            missing = self.client.get('/secretary/receipt/999')
        self.assertEqual(missing.status_code, 404)


if __name__ == '__main__':
    unittest.main()
