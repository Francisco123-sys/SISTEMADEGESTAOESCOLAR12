import importlib
import os
import sys
import unittest
from unittest.mock import patch


class AppStartupTests(unittest.TestCase):
    def setUp(self):
        self.previous_secret = os.environ.get('SECRET_KEY')
        os.environ['SECRET_KEY'] = 'test-secret-key'

    def tearDown(self):
        if self.previous_secret is None:
            os.environ.pop('SECRET_KEY', None)
        else:
            os.environ['SECRET_KEY'] = self.previous_secret

    def test_import_does_not_initialize_database(self):
        sys.modules.pop('app', None)
        try:
            with patch('database.init_db.init_db') as initialize_database:
                app_module = importlib.import_module('app')

            initialize_database.assert_not_called()
            self.assertIn('init-db', app_module.app.cli.list_commands(app_module.app))
        finally:
            sys.modules.pop('app', None)

    def test_changed_templates_compile(self):
        app_module = importlib.import_module('app')
        for template in (
            'secretary/student_form.html',
            'secretary/student_registration_payment.html',
            'secretary/students.html',
            'secretary/student_edit.html',
            'secretary/guardians.html',
            'secretary/turmas.html',
            'secretary/lista_nominal.html',
            'secretary/guardians.html',
            'secretary/dashboard.html',
            'director/turmas.html',
            'portal_encarregado.html',
        ):
            app_module.app.jinja_env.get_template(template)


if __name__ == '__main__':
    unittest.main()
