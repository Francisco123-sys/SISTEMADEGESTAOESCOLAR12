import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash

os.environ.setdefault('SECRET_KEY', 'activity-endpoint-test-key')

from app import app
from database import init_db as database_init
import services.db as db_service


DATABASE_URL_VARIABLES = (
    'DATABASE_URL',
    'POSTGRES_URL',
    'POSTGRES_PRISMA_URL',
    'DATABASE_URL_UNPOOLED',
    'POSTGRES_URL_NON_POOLING',
)


class ActiveUserEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temporary_directory.name) / 'sige-test.db')
        self.environment = patch.dict(os.environ, {
            **{name: '' for name in DATABASE_URL_VARIABLES},
            'SIGE_INITIAL_ADMIN_EMAIL': '',
            'SIGE_INITIAL_ADMIN_PASSWORD': '',
        })
        self.environment.start()
        self.database_path_patch = patch.object(
            database_init, 'SQLITE_DATABASE_PATH', self.database_path
        )
        self.database_path_patch.start()
        self.runtime_database_patch = patch.object(
            db_service, 'SQLITE_DATABASE', self.database_path
        )
        self.runtime_database_patch.start()
        with patch.object(database_init, '_is_pg', return_value=False):
            database_init.init_db()

        self.password = 'activity-test-password'
        password_hash = generate_password_hash(self.password)
        staff_roles = (
            ('ADM-TEST', 'admin'),
            ('DIR-TEST', 'director'),
            ('PED1-TEST', 'pedagogico_1'),
            ('PED2-TEST', 'pedagogico_2'),
            ('PROF-TEST', 'teacher'),
            ('SEC-TEST', 'secretary'),
            ('CHEFE-TEST', 'chefe_secretaria'),
        )
        connection = sqlite3.connect(self.database_path)
        connection.executemany('''
            INSERT INTO users (code, nome, email, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
        ''', [
            (
                code, f'Utilizador {role}', f'{role}@example.test',
                password_hash, role,
            )
            for code, role in staff_roles
        ])
        connection.execute('''
            INSERT INTO guardians (nome, email, password_hash, contacto)
            VALUES (?, ?, ?, ?)
        ''', ('Encarregado de Teste', 'guardian@example.test', password_hash, '840000000'))
        connection.commit()
        connection.close()

        self.previous_testing = app.config.get('TESTING')
        self.previous_secure_cookie = app.config.get('SESSION_COOKIE_SECURE')
        app.config.update(
            TESTING=True,
            SECRET_KEY='activity-endpoint-test-key',
            SESSION_COOKIE_SECURE=False,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config['TESTING'] = self.previous_testing
        app.config['SESSION_COOKIE_SECURE'] = self.previous_secure_cookie
        self.runtime_database_patch.stop()
        self.database_path_patch.stop()
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _csrf_token(self):
        with self.client.session_transaction() as browser_session:
            return browser_session['_csrf_token']

    def _staff_login(self, profile, code):
        self.client.get('/')
        response = self.client.post('/login', data={
            'csrf_token': self._csrf_token(),
            'perfil': profile,
            'identificador': code,
            'password': self.password,
        })
        self.assertEqual(response.status_code, 302)

    def test_public_login_has_no_presence_data_and_dashboards_remain_protected(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('REPÚBLICA DE MOÇAMBIQUE', html)
        self.assertNotIn('data-active-users', html)
        self.assertNotIn('Utilizadores activos', html)
        for path in (
            '/admin/dashboard',
            '/director/dashboard',
            '/secretary/dashboard',
            '/teacher/dashboard',
        ):
            self.assertEqual(self.client.get(path).status_code, 302)
        self.assertEqual(self.client.get('/api/active-users').status_code, 401)

    def test_all_staff_profiles_and_guardian_can_authenticate(self):
        for profile, code in (
            ('admin', 'ADM-TEST'),
            ('director', 'DIR-TEST'),
            ('pedagogico_1', 'PED1-TEST'),
            ('pedagogico_2', 'PED2-TEST'),
            ('teacher', 'PROF-TEST'),
            ('secretary', 'SEC-TEST'),
            ('secretary', 'CHEFE-TEST'),
        ):
            with self.subTest(profile=profile, code=code):
                self._staff_login(profile, code)
                with self.client.session_transaction() as browser_session:
                    self.assertIn('user_id', browser_session)

        self.client.get('/portal-encarregado')
        guardian_response = self.client.post('/portal-encarregado', data={
            'csrf_token': self._csrf_token(),
            'acao': 'login',
            'email': 'guardian@example.test',
            'password': self.password,
        })
        self.assertEqual(guardian_response.status_code, 302)
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session.get('guardian_role'), 'guardian')

    def test_counts_are_authenticated_details_are_authorized_and_tabs_deduplicate(self):
        self._staff_login('admin', 'ADM-TEST')
        csrf_token = self._csrf_token()
        for _ in range(2):
            heartbeat = self.client.post(
                '/api/active-users/heartbeat',
                headers={'X-CSRF-Token': csrf_token},
            )
            self.assertEqual(heartbeat.status_code, 200)

        counts = self.client.get('/api/active-users')
        self.assertEqual(counts.status_code, 200)
        admin_count = next(
            item['count'] for item in counts.json['sections']
            if item['section'] == 'Administrador'
        )
        self.assertEqual(admin_count, 1)

        details = self.client.get('/api/active-users/details')
        self.assertEqual(details.status_code, 200)
        self.assertEqual(len(details.json['sessions']), 1)
        self.assertEqual(details.json['sessions'][0]['status'], 'Activa')

        self._staff_login('teacher', 'PROF-TEST')
        self.assertEqual(self.client.get('/api/active-users/details').status_code, 403)

    def test_admin_can_filter_and_view_audit_details_without_credentials(self):
        self._staff_login('admin', 'ADM-TEST')
        response = self.client.get('/admin/dashboard?module=Autenticação&operation=LOGIN')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Ver Detalhes', response.get_data(as_text=True))
        connection = sqlite3.connect(self.database_path)
        log_id = connection.execute(
            'SELECT id FROM audit_logs WHERE operacao = ? ORDER BY id DESC LIMIT 1',
            ('LOGIN',),
        ).fetchone()[0]
        connection.close()

        details = self.client.get(f'/admin/audit/{log_id}')
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.json['module'], 'Autenticação')
        self.assertNotIn(self.password, str(details.json))


if __name__ == '__main__':
    unittest.main()
