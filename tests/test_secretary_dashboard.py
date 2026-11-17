import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app


class SecretaryDashboardTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()

    def _database_with_dashboard_counts(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [
            {'total': 80},
            {'total': 12},
            {'total': 5},
            {'total': 3},
            {'total': 20},
        ]
        cursor.fetchall.return_value = []
        db = Mock()
        db.cursor.return_value = cursor
        return db

    def test_dashboard_shows_database_indicators_and_real_shortcuts(self):
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = 1
            browser_session['user_nome'] = 'Secretaria de Teste'

        with (
            patch(
                'routes.secretary.get_db',
                return_value=self._database_with_dashboard_counts(),
            ),
            patch('routes.secretary.active_student_condition', return_value='ativo = 1'),
        ):
            response = self.client.get('/secretary/dashboard')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Total de alunos matriculados', response.data)
        self.assertIn(b'>80</span>', response.data)
        self.assertIn(b'Total de turmas', response.data)
        self.assertIn(b'>12</span>', response.data)
        self.assertIn(b'Alunos isentos de taxa', response.data)
        self.assertIn(b'>5</span>', response.data)
        self.assertIn(b'Alunos com NEE', response.data)
        self.assertIn(b'>3</span>', response.data)
        self.assertIn(b'Pagamentos registados', response.data)
        self.assertIn(b'>20</span>', response.data)
        self.assertIn(b'Emitir listas nominais', response.data)
        self.assertIn(b'Gerir encarregados', response.data)
        self.assertIn(b'Nenhum pagamento registado.', response.data)
        self.assertIn(b'class="secretary-icon-svg"', response.data)
        self.assertNotRegex(
            response.get_data(as_text=True),
            r'[\U0001F300-\U0001FAFF\u2600-\u27BF]',
        )
        self.assertNotIn(b'Receita registada', response.data)
        self.assertNotIn(b'Gerir colegas', response.data)

    def test_dashboard_shows_revenue_only_to_authorized_roles(self):
        db = self._database_with_dashboard_counts()
        db.cursor.return_value.fetchone.side_effect = [
            {'total': 80},
            {'total': 12},
            {'total': 5},
            {'total': 3},
            {'total': 20},
            {'total': 2450.50},
        ]
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'chefe_secretaria'
            browser_session['user_id'] = 1

        with (
            patch('routes.secretary.get_db', return_value=db),
            patch('routes.secretary.active_student_condition', return_value='ativo = 1'),
        ):
            response = self.client.get('/secretary/dashboard')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Receita registada', response.data)
        self.assertIn(b'2450.50 MZN', response.data)
        self.assertIn(b'Consultar pautas', response.data)
        self.assertIn(b'Gerir colegas', response.data)


if __name__ == '__main__':
    unittest.main()
