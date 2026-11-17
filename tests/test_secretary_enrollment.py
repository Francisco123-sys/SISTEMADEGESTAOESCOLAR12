import os
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app


class EmptyCursor:
    def __init__(self):
        self.statement = ''

    def execute(self, *_args, **_kwargs):
        self.statement = _args[0]
        return self

    def fetchone(self):
        if 'FROM academic_years' in self.statement:
            return {'id': 1}
        return None


class EmptyDatabase:
    def cursor(self):
        return EmptyCursor()


class SecretaryEnrollmentTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret-key')
        self.client = app.test_client()
        with self.client.session_transaction() as browser_session:
            browser_session['user_role'] = 'secretary'
            browser_session['user_id'] = 7
            browser_session['_csrf_token'] = 'test-csrf-token'

    def post_enrollment(self, **overrides):
        form = {
            'nome': 'Aluno de Teste',
            'bi_cedula': 'TEST-123',
            'data_nascimento': '2008-01-01',
            'sexo': 'M',
            'encarregado_parentesco': 'Outro',
            'encarregado_nome': 'Encarregado Teste',
            'classe': '8',
            'turno': 'Manhã',
            'curso': 'Diurno',
            'csrf_token': 'test-csrf-token',
        }
        form.update(overrides)
        with patch('routes.secretary.get_db', return_value=EmptyDatabase()):
            return self.client.post('/secretary/students/new', data=form)

    def test_age_does_not_override_manually_selected_class(self):
        response = self.post_enrollment()

        self.assertEqual(response.status_code, 302)
        self.assertIn('/secretary/students/new/payment', response.headers['Location'])
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session['pending_matricula']['classe'], 8)

    def test_second_cycle_requires_a_valid_area(self):
        response = self.post_enrollment(classe='10')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/secretary/students/new'))
        with self.client.session_transaction() as browser_session:
            self.assertNotIn('pending_matricula', browser_session)

    def test_new_enrollment_rejects_legacy_night_shift(self):
        response = self.post_enrollment(turno='Noite')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/secretary/students/new'))


if __name__ == '__main__':
    unittest.main()
