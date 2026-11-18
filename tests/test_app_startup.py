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

    def test_application_uses_environment_secret_for_signed_sessions(self):
        app_module = importlib.import_module('app')
        self.assertEqual(app_module.app.secret_key, 'test-secret-key')

        client = app_module.app.test_client()
        with client.session_transaction() as flask_session:
            flask_session['session_probe'] = 'signed'

        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as flask_session:
            self.assertEqual(flask_session['session_probe'], 'signed')

    def test_missing_secret_key_fails_with_clear_message(self):
        sys.modules.pop('app', None)
        try:
            with patch.dict(os.environ, {'SECRET_KEY': ''}), patch('dotenv.load_dotenv'):
                with self.assertRaisesRegex(
                    RuntimeError,
                    'Defina SECRET_KEY no ambiente antes de iniciar a aplicação.',
                ):
                    importlib.import_module('app')
        finally:
            sys.modules.pop('app', None)

    def test_render_startup_requires_postgresql_configuration(self):
        sys.modules.pop('app', None)
        database_variables = (
            'DATABASE_URL',
            'DATABASE_URL_UNPOOLED',
            'POSTGRES_URL',
            'POSTGRES_PRISMA_URL',
            'POSTGRES_URL_NON_POOLING',
        )
        try:
            with patch.dict(
                os.environ,
                {
                    'SECRET_KEY': 'test-secret-key',
                    'RENDER': 'true',
                    **{name: '' for name in database_variables},
                },
            ), patch('dotenv.load_dotenv'):
                with self.assertRaisesRegex(
                    RuntimeError,
                    'Defina DATABASE_URL no ambiente do Render antes de iniciar a aplicação.',
                ):
                    importlib.import_module('app')
        finally:
            sys.modules.pop('app', None)

    def test_render_startup_rejects_malformed_database_url(self):
        sys.modules.pop('app', None)
        try:
            with patch.dict(
                os.environ,
                {
                    'SECRET_KEY': 'test-secret-key',
                    'RENDER': 'true',
                    'DATABASE_URL': 'postgresql://',
                },
            ), patch('dotenv.load_dotenv'):
                with self.assertRaisesRegex(
                    RuntimeError,
                    'DATABASE_URL deve conter uma ligação PostgreSQL válida no Render.',
                ):
                    importlib.import_module('app')
        finally:
            sys.modules.pop('app', None)

    def test_render_startup_accepts_environment_configuration(self):
        sys.modules.pop('app', None)
        database_url = 'postgresql://db.example.test/school'
        try:
            with patch.dict(
                os.environ,
                {
                    'SECRET_KEY': 'existing-render-key',
                    'RENDER': 'true',
                    'DATABASE_URL': database_url,
                },
            ), patch('dotenv.load_dotenv'):
                app_module = importlib.import_module('app')

            self.assertEqual(app_module.app.secret_key, 'existing-render-key')
        finally:
            sys.modules.pop('app', None)

    def test_changed_templates_compile(self):
        app_module = importlib.import_module('app')
        for template in (
            'components/dashboard_ui.html',
            'login.html',
            'teacher/dashboard.html',
            'teacher/grades.html',
            'secretary/student_form.html',
            'secretary/student_registration_payment.html',
            'secretary/students.html',
            'secretary/student_edit.html',
            'secretary/guardians.html',
            'secretary/turmas.html',
            'secretary/lista_nominal.html',
            'secretary/guardians.html',
            'secretary/dashboard.html',
            'director/dashboard.html',
            'admin/dashboard.html',
            'director/turmas.html',
            'portal_encarregado.html',
        ):
            app_module.app.jinja_env.get_template(template)

    def test_login_page_renders_two_column_profile_selector(self):
        app_module = importlib.import_module('app')
        app_module.app.config.update(TESTING=True)

        client = app_module.app.test_client()
        response = client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('sige-login-layout', html)
        self.assertIn('sige-login-sidebar', html)
        self.assertIn('sige-login-content', html)
        self.assertIn('data-profile="guardian"', html)

        css_response = client.get('/static/css/style.css?v=20261117-2')
        self.assertEqual(css_response.status_code, 200)
        css = css_response.get_data(as_text=True)
        css_response.close()
        self.assertIn('.sige-login-layout', css)
        self.assertIn('.teacher-assignment-grid', css)
        self.assertIn('education-tech.svg', css)
        image_response = client.get('/static/img/education-tech.svg')
        self.assertEqual(image_response.status_code, 200)
        self.assertIn(b'educa', image_response.data.lower())

    def test_shared_dashboard_header_renders_for_director_and_admin(self):
        from flask import render_template, session

        app_module = importlib.import_module('app')
        with app_module.app.test_request_context('/director/dashboard'):
            session['user_role'] = 'pedagogico_1'
            session['user_nome'] = 'Direcção de Teste'
            director_html = render_template(
                'director/dashboard.html',
                total_alunos=0,
                total_professores=0,
                total_turmas=0,
                total_receita=0,
                pedidos_eliminacao=[],
                funcionarios=[],
            )
        with app_module.app.test_request_context('/admin/dashboard'):
            admin_html = render_template(
                'admin/dashboard.html',
                logs=[],
                filters={
                    'module': '',
                    'operation': '',
                    'user': '',
                    'date_from': '',
                    'date_to': '',
                },
            )

        self.assertIn('sige-topbar', director_html)
        self.assertIn('Direcção Escolar', director_html)
        self.assertIn('Direcção Pedagógica I', director_html)
        self.assertIn('sige-topbar-menu', director_html)
        self.assertIn('sige-topbar', admin_html)
        self.assertIn('Histórico de Actividades', admin_html)


if __name__ == '__main__':
    unittest.main()
