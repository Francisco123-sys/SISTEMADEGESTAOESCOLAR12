import os
import sqlite3
import hashlib
import smtplib
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

os.environ.setdefault('SECRET_KEY', 'password-recovery-test-secret')

from werkzeug.security import check_password_hash, generate_password_hash

from app import app


class GuardianPasswordRecoveryTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='password-recovery-test-secret')
        self.client = app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        with open('database/schema.sql', encoding='utf-8') as schema:
            self.db.executescript(schema.read())
        self.db.execute('''
            INSERT INTO guardians (
                id, nome, email, password_hash, contacto, ativo, session_version
            )
            VALUES (1, 'Encarregada Teste', 'parent@example.test', ?, '840000000', 1, 0)
        ''', (generate_password_hash('guardian-password-123'),))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _csrf(self):
        self.client.get('/portal-encarregado/recuperar')
        with self.client.session_transaction() as browser_session:
            return browser_session['_csrf_token']

    def test_recovery_response_does_not_disclose_account_existence(self):
        with patch('routes.auth.get_db', return_value=self.db):
            unknown_token = self._csrf()
            unknown = self.client.post(
                '/portal-encarregado/recuperar',
                data={'csrf_token': unknown_token, 'email': 'unknown@example.test'},
                follow_redirects=True,
            )
            known_token = self._csrf()
            with (
                patch.dict(os.environ, {
                    'APP_BASE_URL': 'https://school.example.test',
                    'SMTP_HOST': 'smtp.example.test',
                    'SMTP_FROM': 'school@example.test',
                }),
                patch('routes.auth.send_password_reset_email'),
            ):
                known = self.client.post(
                    '/portal-encarregado/recuperar',
                    data={
                        'csrf_token': known_token,
                        'email': 'parent@example.test',
                    },
                    follow_redirects=True,
                )

        generic_message = b'Se existir uma conta activa, ser\xc3\xa1 enviada uma liga\xc3\xa7\xc3\xa3o de recupera\xc3\xa7\xc3\xa3o.'
        self.assertIn(generic_message, unknown.data)
        self.assertIn(generic_message, known.data)

    def test_reset_token_is_hashed_single_use_and_revokes_sessions(self):
        csrf_token = self._csrf()
        with (
            patch('routes.auth.get_db', return_value=self.db),
            patch.dict(os.environ, {
                'APP_BASE_URL': 'https://school.example.test',
                'SMTP_HOST': 'smtp.example.test',
                'SMTP_FROM': 'school@example.test',
            }),
            patch('routes.auth.send_password_reset_email') as send_email,
        ):
            requested = self.client.post(
                '/portal-encarregado/recuperar',
                data={
                    'csrf_token': csrf_token,
                    'email': 'parent@example.test',
                },
            )
            self.assertEqual(requested.status_code, 302)
            reset_url = send_email.call_args.args[1]
            token = urlparse(reset_url).path.rsplit('/', 1)[-1]
            self.assertRegex(token, r'^[A-Za-z0-9_-]{40,}$')
            self.assertEqual(
                self.db.execute(
                    'SELECT token_hash FROM guardian_password_resets'
                ).fetchone()['token_hash'],
                hashlib.sha256(token.encode('utf-8')).hexdigest(),
            )
            form = self.client.get(urlparse(reset_url).path)
            self.assertEqual(form.status_code, 200)
            with self.client.session_transaction() as browser_session:
                csrf_token = browser_session['_csrf_token']
            changed = self.client.post(urlparse(reset_url).path, data={
                'csrf_token': csrf_token,
                'new_password': 'replacement-guardian-password',
                'confirm_password': 'replacement-guardian-password',
            })
            reused = self.client.get(urlparse(reset_url).path)

        self.assertEqual(changed.status_code, 302)
        self.assertEqual(reused.status_code, 302)
        guardian = self.db.execute(
            'SELECT password_hash, session_version FROM guardians WHERE id = 1'
        ).fetchone()
        self.assertTrue(check_password_hash(
            guardian['password_hash'],
            'replacement-guardian-password',
        ))
        self.assertEqual(guardian['session_version'], 1)

    def test_superseded_reset_link_cannot_be_used(self):
        with (
            patch('routes.auth.get_db', return_value=self.db),
            patch.dict(os.environ, {
                'APP_BASE_URL': 'https://school.example.test',
                'SMTP_HOST': 'smtp.example.test',
                'SMTP_FROM': 'school@example.test',
            }),
            patch('routes.auth.send_password_reset_email') as send_email,
        ):
            for _ in range(2):
                csrf_token = self._csrf()
                self.client.post('/portal-encarregado/recuperar', data={
                    'csrf_token': csrf_token,
                    'email': 'parent@example.test',
                })
            first_token = urlparse(send_email.call_args_list[0].args[1]).path
            second_token = urlparse(send_email.call_args_list[1].args[1]).path
            old_link = self.client.get(first_token)
            current_link = self.client.get(second_token)

        self.assertEqual(old_link.status_code, 302)
        self.assertEqual(current_link.status_code, 200)

    def test_expired_reset_token_is_rejected(self):
        digest = hashlib.sha256(b'expired-test-token').hexdigest()
        self.db.execute('''
            INSERT INTO guardian_password_resets (
                token_hash, guardian_id, expires_at
            ) VALUES (?, 1, 1)
        ''', (digest,))
        self.db.commit()
        with patch('routes.auth.get_db', return_value=self.db):
            response = self.client.get(
                '/portal-encarregado/redefinir/expired-test-token'
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.db.execute(
                'SELECT session_version FROM guardians WHERE id = 1'
            ).fetchone()['session_version'],
            0,
        )

    def test_smtp_failure_invalidates_issued_token(self):
        csrf_token = self._csrf()
        with (
            patch('routes.auth.get_db', return_value=self.db),
            patch.dict(os.environ, {
                'APP_BASE_URL': 'https://school.example.test',
                'SMTP_HOST': 'smtp.example.test',
                'SMTP_FROM': 'school@example.test',
            }),
            patch(
                'routes.auth.send_password_reset_email',
                side_effect=smtplib.SMTPException('delivery failed'),
            ),
        ):
            response = self.client.post(
                '/portal-encarregado/recuperar',
                data={
                    'csrf_token': csrf_token,
                    'email': 'parent@example.test',
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        reset = self.db.execute(
            'SELECT used_at FROM guardian_password_resets'
        ).fetchone()
        self.assertIsNotNone(reset['used_at'])

    def test_recovery_attempts_are_rate_limited(self):
        with (
            patch('routes.auth.get_db', return_value=self.db),
            patch.dict(os.environ, {
                'APP_BASE_URL': 'https://school.example.test',
                'SMTP_HOST': 'smtp.example.test',
                'SMTP_FROM': 'school@example.test',
            }),
            patch('routes.auth.send_password_reset_email') as send_email,
        ):
            for _ in range(4):
                csrf_token = self._csrf()
                response = self.client.post(
                    '/portal-encarregado/recuperar',
                    data={
                        'csrf_token': csrf_token,
                        'email': 'parent@example.test',
                    },
                )
                self.assertEqual(response.status_code, 302)

        self.assertEqual(send_email.call_count, 3)

    def test_staff_login_rate_limit_blocks_repeated_bad_passwords(self):
        remote = {'REMOTE_ADDR': '198.51.100.41'}
        with patch('routes.auth.get_db', return_value=self.db):
            self.client.get('/')
            with self.client.session_transaction() as browser_session:
                csrf_token = browser_session['_csrf_token']
            for _ in range(5):
                response = self.client.post(
                    '/login',
                    environ_overrides=remote,
                    data={
                        'csrf_token': csrf_token,
                        'perfil': 'teacher',
                        'identificador': 'unknown-teacher',
                        'password': 'invalid-password',
                    },
                )
                self.assertEqual(response.status_code, 302)

        with app.test_request_context('/', environ_overrides=remote):
            from services.auth_security import is_login_rate_limited

            self.assertTrue(is_login_rate_limited(
                self.db.cursor(),
                'unknown-teacher',
            ))

    def test_staff_login_falls_back_when_rate_limit_table_is_missing(self):
        remote = {'REMOTE_ADDR': '198.51.100.42'}
        self.db.execute('DROP TABLE auth_rate_limits')
        with patch('routes.auth.get_db', return_value=self.db):
            self.client.get('/')
            with self.client.session_transaction() as browser_session:
                csrf_token = browser_session['_csrf_token']
            for _ in range(5):
                response = self.client.post(
                    '/login',
                    environ_overrides=remote,
                    data={
                        'csrf_token': csrf_token,
                        'perfil': 'teacher',
                        'identificador': 'fallback-teacher',
                        'password': 'invalid-password',
                    },
                )
                self.assertEqual(response.status_code, 302)

        with app.test_request_context('/', environ_overrides=remote):
            from services.auth_security import is_login_rate_limited

            self.assertTrue(is_login_rate_limited(
                self.db.cursor(),
                'fallback-teacher',
            ))

    def test_unknown_recovery_attempts_are_committed_and_limited(self):
        class TrackedDatabase:
            def __init__(self, connection):
                self.connection = connection
                self.commits = 0

            def cursor(self):
                return self.connection.cursor()

            def commit(self):
                self.commits += 1
                self.connection.commit()

        tracked_db = TrackedDatabase(self.db)
        with (
            patch('routes.auth.get_db', return_value=tracked_db),
            patch('routes.auth.send_password_reset_email') as send_email,
        ):
            for _ in range(4):
                csrf_token = self._csrf()
                response = self.client.post(
                    '/portal-encarregado/recuperar',
                    data={
                        'csrf_token': csrf_token,
                        'email': 'unknown-parent@example.test',
                    },
                )
                self.assertEqual(response.status_code, 302)

        self.assertEqual(tracked_db.commits, 3)
        send_email.assert_not_called()

    def test_login_rate_limit_blocks_repeated_bad_passwords(self):
        csrf_token = self._csrf()
        with patch('routes.auth.get_db', return_value=self.db):
            for _ in range(5):
                response = self.client.post('/portal-encarregado', data={
                    'csrf_token': csrf_token,
                    'acao': 'login',
                    'email': 'parent@example.test',
                    'password': 'wrong-password',
                })
                self.assertEqual(response.status_code, 302)
            blocked = self.client.post('/portal-encarregado', data={
                'csrf_token': csrf_token,
                'acao': 'login',
                'email': 'parent@example.test',
                'password': 'guardian-password-123',
            })

        self.assertEqual(blocked.status_code, 302)
        with self.client.session_transaction() as browser_session:
            self.assertNotIn('guardian_id', browser_session)


if __name__ == '__main__':
    unittest.main()
